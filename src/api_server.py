"""
api_server.py — Asynchronous, multi-user OCR API backend (FastAPI).

Design (steps 1-3)
------------------
1. Non-blocking: OCR runs via run_in_threadpool so the event loop stays free.
2. Multi-worker: served by Gunicorn + Uvicorn workers (see Dockerfile.api).
3. Shared state: job progress/result stored in Redis via job_store, so any
   worker can answer /ocr/{id}/status and /ocr/{id}/result.

Job flow
--------
    POST /ocr                    -> validate, save file, queue job, return job_id
    GET  /ocr/{id}/status        -> live progress (per page)
    GET  /ocr/{id}/result        -> final text + files when completed
    GET  /health                 -> liveness
    GET  /ready                  -> readiness (Tesseract available)
    GET  /languages              -> installed languages

Gemini
------
Controlled ONLY by backend env (GEMINI_ENABLED + GEMINI_API_KEY). Never exposed
to the interface. If disabled or failing, the pipeline uses Tesseract + cv2.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

import exporters as EX
import table_export as TX
import table_format as TF
import job_store
import llm_enhance
from ocr import IMAGE_EXTS, PDF_EXTS, OCR, available_languages


# ---------------------------------------------------------------------------
# Logging & configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

LOG = logging.getLogger("ocr_api")

ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

SERVER_OCR_WORKERS = int(os.getenv("OCR_MAX_WORKERS", "0"))

OCR_WORKDIR = Path(os.getenv("OCR_WORKDIR", "/tmp/ocr-api"))
OCR_WORKDIR.mkdir(parents=True, exist_ok=True)

CORS_ORIGINS = [
    v.strip() for v in os.getenv("CORS_ORIGINS", "").split(",") if v.strip()
]

ALLOWED_MODES = {"auto", "document", "photo", "lowcontrast", "inverted", "none"}
ALLOWED_LOCALES = {"auto", "dot", "comma"}
ALLOWED_FORMATS = set(EX.ALL_FORMATS)
SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OCR Toolkit API",
    version="2.0.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
elif not IS_PRODUCTION:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_filename(filename: str) -> str:
    original = Path(filename or "upload").name
    cleaned = SAFE_FILENAME_PATTERN.sub("_", original).strip("._")
    return cleaned or "upload"


def validate_file_signature(content: bytes, extension: str) -> None:
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(415, "Invalid PDF signature.")
        return

    signatures = {
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".jpg": (b"\xff\xd8\xff",),
        ".jpeg": (b"\xff\xd8\xff",),
        ".bmp": (b"BM",),
        ".tif": (b"II*\x00", b"MM\x00*"),
        ".tiff": (b"II*\x00", b"MM\x00*"),
        ".webp": (b"RIFF",),
    }

    expected = signatures.get(extension)
    if expected is None:
        return

    if not any(content.startswith(sig) for sig in expected):
        raise HTTPException(
            415, f"File content does not match {extension}."
        )


def validate_options(lang, mode, psm, locale, formats) -> list[str]:
    if mode not in ALLOWED_MODES:
        raise HTTPException(422, f"Unsupported mode: {mode}")
    if locale not in ALLOWED_LOCALES:
        raise HTTPException(422, f"Unsupported locale: {locale}")
    if not 3 <= psm <= 13:
        raise HTTPException(422, "PSM must be between 3 and 13.")

    requested = {v.strip().lower() for v in formats.split(",") if v.strip()}
    invalid = requested - ALLOWED_FORMATS
    if invalid:
        raise HTTPException(
            422, "Unsupported format(s): " + ", ".join(sorted(invalid))
        )

    chosen = [v for v in EX.ALL_FORMATS if v in requested]
    if not chosen:
        raise HTTPException(422, "Select at least one output format.")

    installed = set(available_languages())
    requested_langs = {v.strip() for v in lang.split("+") if v.strip()}
    missing = requested_langs - installed
    if missing:
        raise HTTPException(
            422, "Missing Tesseract language(s): " + ", ".join(sorted(missing))
        )

    return chosen


def resolve_workers(requested: int) -> int:
    if requested < 0:
        raise HTTPException(422, "workers must be >= 0.")
    if requested > 0:
        return requested
    if SERVER_OCR_WORKERS > 0:
        return SERVER_OCR_WORKERS
    return max(1, (os.cpu_count() or 2) - 1)


def encode_file(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.is_file():
        return None
    return base64.b64encode(file_path.read_bytes()).decode("ascii")


def files_payload(made: dict) -> dict:
    return {
        fmt: {"name": Path(p).name, "b64": encode_file(p)}
        for fmt, p in made.items()
    }


# ---------------------------------------------------------------------------
# Background OCR job (blocking work, run off the event loop)
# ---------------------------------------------------------------------------

def run_ocr_job(
    job_id: str,
    job_dir: str,
    local_file: str,
    lang: str,
    mode: str,
    psm: int,
    detect_tables: bool,
    table_mode: bool,
    locale: str,
    formats: list[str],
    fast: bool,
) -> None:
    job_directory = Path(job_dir)
    source = Path(local_file)

    try:
        # ---- Table mode ----
        if table_mode:
            result = TF.extract_table_from_file(
                str(source), lang=lang, psm=psm, locale=locale
            )
            if not result.found:
                job_store.set_result(job_id, {
                    "ok": True,
                    "status": "No table detected.",
                    "text": "",
                    "files": {},
                    "engine": "tesseract",
                })
                return

            df = result.to_dataframe()
            made = EX.export_all(
                job_directory, source.stem,
                text=result.aligned_text, table_df=df,
                summary={"method": result.method}, formats=formats,
                locale=locale,
            )
            job_store.set_result(job_id, {
                "ok": True,
                "status": f"Table mode ({result.method}).",
                "text": result.aligned_text,
                "table": (
                    [list(df.columns)] + df.astype(str).values.tolist()
                    if not df.empty else None
                ),
                "files": files_payload(made),
                "engine": "tesseract",
            })
            return

        # ---- Normal OCR ----
        ocr = OCR(
            lang=lang, mode=mode, psm=psm,
            detect_tables=detect_tables, locale=locale,
            fast=fast, workers=resolve_workers(0),
        )

        def progress(completed: int, total: int) -> None:
            job_store.update_progress(job_id, completed, total, engine="tesseract")

        doc = ocr.process(source, progress_callback=progress)

        text = doc.markdown or ""

        # ---- Optional Gemini enhancement (backend-gated, fail-proof) ----
        engine = "tesseract"
        enhanced_text, engine = llm_enhance.enhance_text(text, language_hint=lang)
        text = enhanced_text

        written = ocr.export(doc, job_directory, source.stem)

        table_df = None
        table_preview = None
        if doc.tables:
            dfs = TX.html_to_dataframes(doc.tables[0].html)
            if dfs:
                table_df, _ = TX.validate_table(dfs[0], 0, locale=locale)
                table_preview = (
                    [list(table_df.columns)]
                    + table_df.astype(str).values.tolist()
                )

        made = EX.export_all(
            job_directory, source.stem, text=text,
            table_df=table_df, summary=written.get("summary"),
            formats=formats, locale=locale,
        )

        job_store.set_result(job_id, {
            "ok": True,
            "status": (
                f"Completed {doc.pages} page(s), "
                f"{written['summary']['n_tables']} table(s)."
            ),
            "detected_mode": doc.detected_mode,
            "mean_confidence": doc.mean_confidence,
            "review_recommended": doc.review_recommended,
            "text": text,
            "table": table_preview,
            "files": files_payload(made),
            "engine": engine,
        })

    except Exception as exc:
        LOG.exception("OCR job failed job_id=%s", job_id)
        job_store.set_error(job_id, str(exc))

    finally:
        shutil.rmtree(job_directory, ignore_errors=True)


# ---------------------------------------------------------------------------
# Diagnostic endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"service": "OCR Toolkit API", "version": "2.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    languages = available_languages()
    if not languages:
        raise HTTPException(503, "Tesseract not ready.")
    return {"status": "ready", "languages": languages}


@app.get("/languages")
def languages():
    return {"languages": available_languages()}


# ---------------------------------------------------------------------------
# OCR job endpoints
# ---------------------------------------------------------------------------

@app.post("/ocr")
async def submit_ocr(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    lang: str = Form("eng"),
    mode: str = Form("auto"),
    psm: int = Form(6),
    detect_tables: bool = Form(False),
    table_mode: bool = Form(False),
    locale: str = Form("auto"),
    formats: str = Form("md,json"),
    fast: bool = Form(True),
):
    filename = safe_filename(file.filename or "upload")
    extension = Path(filename).suffix.lower()

    if extension not in (IMAGE_EXTS | PDF_EXTS):
        raise HTTPException(415, f"Unsupported file type: {extension}")

    chosen = validate_options(lang, mode, psm, locale, formats)

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Max upload is {MAX_UPLOAD_MB} MB.")

    validate_file_signature(content, extension)

    job_id = uuid.uuid4().hex
    job_directory = OCR_WORKDIR / job_id
    job_directory.mkdir(parents=True, exist_ok=False)
    local_file = job_directory / filename

    # Writing the file is small/fast; run in threadpool to stay non-blocking.
    await run_in_threadpool(local_file.write_bytes, content)

    job_store.create_job(job_id, filename=filename)

    background_tasks.add_task(
        run_ocr_job,
        job_id=job_id,
        job_dir=str(job_directory),
        local_file=str(local_file),
        lang=lang,
        mode=mode,
        psm=psm,
        detect_tables=detect_tables,
        table_mode=table_mode,
        locale=locale,
        formats=chosen,
        fast=fast,
    )

    LOG.info("OCR job queued job_id=%s filename=%s", job_id, filename)

    return {"ok": True, "job_id": job_id, "status": "queued"}


@app.get("/ocr/{job_id}/status")
def job_status(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found (it may have expired).")

    return {
        "job_id": job_id,
        "status": job.get("status", "unknown"),
        "completed_pages": int(job.get("completed_pages", 0)),
        "total_pages": int(job.get("total_pages", 0)),
        "percent": int(job.get("percent", 0)),
        "engine": job.get("engine", ""),
        "error": job.get("error"),
    }


@app.get("/ocr/{job_id}/result")
def job_result(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found (it may have expired).")

    status = job.get("status")

    if status == "error":
        return {"ok": False, "status": "error", "error": job.get("error")}

    if status != "completed":
        return {"ok": False, "status": status, "percent": int(job.get("percent", 0))}

    return {"ok": True, **json.loads(job["result"])}
