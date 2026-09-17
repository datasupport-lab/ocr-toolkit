"""
Production REST API backend for the OCR toolkit.

Endpoints:
    GET  /
    GET  /health
    GET  /ready
    GET  /languages
    POST /ocr

The Streamlit client communicates with this API through the
internal Docker address:

    http://api:8000
"""

from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware

import exporters as EX
import table_export as TX
import table_format as TF
from ocr import (
    IMAGE_EXTS,
    PDF_EXTS,
    OCR,
    available_languages,
)


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(name)s "
        "%(message)s"
    ),
)

LOG = logging.getLogger("ocr_api")


# ---------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------

ENVIRONMENT = os.getenv(
    "ENVIRONMENT",
    "development",
).strip().lower()

IS_PRODUCTION = ENVIRONMENT == "production"

MAX_UPLOAD_MB = int(
    os.getenv("MAX_UPLOAD_MB", "50")
)

MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# Zero means use CPU count minus one.
SERVER_OCR_WORKERS = int(
    os.getenv("OCR_MAX_WORKERS", "0")
)

OCR_WORKDIR = Path(
    os.getenv(
        "OCR_WORKDIR",
        "/tmp/ocr-api",
    )
)

OCR_WORKDIR.mkdir(
    parents=True,
    exist_ok=True,
)


CORS_ORIGINS = [
    value.strip()
    for value in os.getenv(
        "CORS_ORIGINS",
        "",
    ).split(",")
    if value.strip()
]


# ---------------------------------------------------------------------
# Allowed request values
# ---------------------------------------------------------------------

ALLOWED_MODES = {
    "auto",
    "document",
    "photo",
    "lowcontrast",
    "inverted",
    "none",
}

ALLOWED_LOCALES = {
    "auto",
    "dot",
    "comma",
}

ALLOWED_FORMATS = set(EX.ALL_FORMATS)

SAFE_FILENAME_PATTERN = re.compile(
    r"[^A-Za-z0-9._-]+"
)


# ---------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------

app = FastAPI(
    title="OCR Toolkit API",
    version="1.2.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=(
        None
        if IS_PRODUCTION
        else "/openapi.json"
    ),
)


# CORS is mainly required for browser-based clients.
# Streamlit server-to-server requests do not require CORS.
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
elif not IS_PRODUCTION:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------

def safe_filename(filename: str) -> str:
    """
    Remove path components and replace unsafe filename characters.
    """

    original = Path(
        filename or "upload"
    ).name

    cleaned = SAFE_FILENAME_PATTERN.sub(
        "_",
        original,
    ).strip("._")

    return cleaned or "upload"


def validate_file_signature(
    content: bytes,
    extension: str,
) -> None:
    """
    Perform a basic file-signature check.

    This is not malware scanning. It only rejects files whose content
    clearly does not match the declared extension.
    """

    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=415,
                detail=(
                    "The uploaded file does not contain "
                    "a valid PDF signature."
                ),
            )

        return

    image_signatures = {
        ".png": (
            b"\x89PNG\r\n\x1a\n",
        ),
        ".jpg": (
            b"\xff\xd8\xff",
        ),
        ".jpeg": (
            b"\xff\xd8\xff",
        ),
        ".bmp": (
            b"BM",
        ),
        ".tif": (
            b"II*\x00",
            b"MM\x00*",
        ),
        ".tiff": (
            b"II*\x00",
            b"MM\x00*",
        ),
        ".webp": (
            b"RIFF",
        ),
    }

    expected_signatures = image_signatures.get(
        extension
    )

    if expected_signatures is None:
        return

    if not any(
        content.startswith(signature)
        for signature in expected_signatures
    ):
        raise HTTPException(
            status_code=415,
            detail=(
                "The uploaded file content does not "
                f"match the {extension} extension."
            ),
        )

    if extension == ".webp":
        if len(content) < 12 or content[8:12] != b"WEBP":
            raise HTTPException(
                status_code=415,
                detail=(
                    "The uploaded file is not a valid "
                    "WEBP image."
                ),
            )


def validate_options(
    lang: str,
    mode: str,
    psm: int,
    locale: str,
    formats: str,
) -> list[str]:
    """
    Validate OCR and export options.

    Returns the selected output formats in the order defined by
    exporters.ALL_FORMATS.
    """

    if mode not in ALLOWED_MODES:
        raise HTTPException(
            status_code=422,
            detail=(
                "Unsupported preprocessing mode: "
                f"{mode}"
            ),
        )

    if locale not in ALLOWED_LOCALES:
        raise HTTPException(
            status_code=422,
            detail=(
                "Unsupported table number style: "
                f"{locale}"
            ),
        )

    if not 3 <= psm <= 13:
        raise HTTPException(
            status_code=422,
            detail=(
                "Tesseract PSM must be between "
                "3 and 13."
            ),
        )

    requested_formats = {
        value.strip().lower()
        for value in formats.split(",")
        if value.strip()
    }

    invalid_formats = (
        requested_formats - ALLOWED_FORMATS
    )

    if invalid_formats:
        raise HTTPException(
            status_code=422,
            detail=(
                "Unsupported output format(s): "
                + ", ".join(
                    sorted(invalid_formats)
                )
            ),
        )

    chosen_formats = [
        value
        for value in EX.ALL_FORMATS
        if value in requested_formats
    ]

    if not chosen_formats:
        raise HTTPException(
            status_code=422,
            detail=(
                "Select at least one supported "
                "output format."
            ),
        )

    installed_languages = set(
        available_languages()
    )

    requested_languages = {
        value.strip()
        for value in lang.split("+")
        if value.strip()
    }

    if not requested_languages:
        raise HTTPException(
            status_code=422,
            detail=(
                "Select at least one OCR language."
            ),
        )

    missing_languages = (
        requested_languages
        - installed_languages
    )

    if missing_languages:
        raise HTTPException(
            status_code=422,
            detail=(
                "Tesseract language data is not "
                "installed for: "
                + ", ".join(
                    sorted(missing_languages)
                )
            ),
        )

    return chosen_formats


def resolve_workers(
    requested_workers: int,
) -> int:
    """
    Resolve the page-worker count.

    Priority:
        1. Positive workers value supplied by the API caller.
        2. Positive OCR_MAX_WORKERS environment setting.
        3. Automatic CPU count minus one.
    """

    if requested_workers < 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "workers must be zero or greater."
            ),
        )

    if requested_workers > 0:
        return requested_workers

    if SERVER_OCR_WORKERS > 0:
        return SERVER_OCR_WORKERS

    return max(
        1,
        (os.cpu_count() or 2) - 1,
    )


def encode_file(
    path: Optional[str],
) -> Optional[str]:
    """
    Read an output file and return Base64 text.
    """

    if not path:
        return None

    file_path = Path(path)

    if not file_path.is_file():
        return None

    return base64.b64encode(
        file_path.read_bytes()
    ).decode("ascii")


def files_payload(
    made: dict[str, str],
) -> dict[str, dict[str, Optional[str]]]:
    """
    Convert output paths into the response format expected by the
    current Streamlit client.
    """

    payload = {}

    for file_format, path in made.items():
        file_path = Path(path)

        payload[file_format] = {
            "name": file_path.name,
            "b64": encode_file(path),
        }

    return payload


# ---------------------------------------------------------------------
# Diagnostic endpoints
# ---------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "OCR Toolkit API",
        "status": "running",
        "version": "1.2.0",
    }


@app.get("/health")
def health():
    """
    Lightweight liveness check.
    """

    return {
        "status": "ok",
    }


@app.get("/ready")
def ready():
    """
    Readiness check that confirms Tesseract languages are visible.
    """

    languages = available_languages()

    if not languages:
        raise HTTPException(
            status_code=503,
            detail=(
                "Tesseract is not ready or no "
                "languages are installed."
            ),
        )

    return {
        "status": "ready",
        "languages": languages,
    }


@app.get("/languages")
def languages():
    return {
        "languages": available_languages(),
    }


# ---------------------------------------------------------------------
# OCR endpoint
# ---------------------------------------------------------------------

@app.post("/ocr")
async def ocr_endpoint(
    file: UploadFile = File(...),
    lang: str = Form("eng"),
    mode: str = Form("auto"),
    psm: int = Form(6),
    detect_tables: bool = Form(False),
    table_mode: bool = Form(False),
    locale: str = Form("auto"),
    formats: str = Form("md,xlsx,json"),
    fast: bool = Form(True),
    workers: int = Form(0),
):
    job_id = uuid.uuid4().hex
    job_directory: Optional[Path] = None

    try:
        filename = safe_filename(
            file.filename or "upload"
        )

        extension = Path(
            filename
        ).suffix.lower()

        if extension not in (
            IMAGE_EXTS | PDF_EXTS
        ):
            raise HTTPException(
                status_code=415,
                detail=(
                    "Unsupported file type: "
                    f"{extension or 'no extension'}"
                ),
            )

        chosen_formats = validate_options(
            lang=lang,
            mode=mode,
            psm=psm,
            locale=locale,
            formats=formats,
        )

        worker_count = resolve_workers(
            workers
        )

        content = await file.read(
            MAX_UPLOAD_BYTES + 1
        )

        if not content:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The uploaded file is empty."
                ),
            )

        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    "The uploaded file exceeds "
                    f"the {MAX_UPLOAD_MB} MB limit."
                ),
            )

        validate_file_signature(
            content=content,
            extension=extension,
        )

        job_directory = (
            OCR_WORKDIR / job_id
        )

        job_directory.mkdir(
            parents=True,
            exist_ok=False,
        )

        local_file = (
            job_directory / filename
        )

        local_file.write_bytes(content)

        LOG.info(
            "OCR job started job_id=%s "
            "filename=%s size_bytes=%s "
            "lang=%s mode=%s table_mode=%s "
            "workers=%s",
            job_id,
            filename,
            len(content),
            lang,
            mode,
            table_mode,
            worker_count,
        )

        # -------------------------------------------------------------
        # Forced table mode
        # -------------------------------------------------------------

        if table_mode:
            result = TF.extract_table_from_file(
                str(local_file),
                lang=lang,
                psm=psm,
                locale=locale,
            )

            if not result.found:
                return {
                    "ok": False,
                    "job_id": job_id,
                    "status": (
                        "No table was detected. "
                        "Turn off Table mode and "
                        "run normal OCR."
                    ),
                    "files": {},
                }

            dataframe = result.to_dataframe()

            made = EX.export_all(
                job_directory,
                local_file.stem,
                text=result.aligned_text,
                table_df=dataframe,
                summary={
                    "source": filename,
                    "method": result.method,
                    "table_mode": True,
                },
                formats=chosen_formats,
                locale=locale,
            )

            output_files = files_payload(made)

            LOG.info(
                "OCR table job completed "
                "job_id=%s method=%s",
                job_id,
                result.method,
            )

            return {
                "ok": True,
                "job_id": job_id,
                "status": (
                    "Table mode completed using "
                    f"{result.method} extraction."
                ),
                "detected_mode": None,
                "text": result.aligned_text,
                "table": (
                    [list(dataframe.columns)]
                    + dataframe.astype(
                        str
                    ).values.tolist()
                    if not dataframe.empty
                    else None
                ),
                "files": output_files,
            }

        # -------------------------------------------------------------
        # Normal OCR mode
        # -------------------------------------------------------------

        ocr = OCR(
            lang=lang,
            mode=mode,
            psm=psm,
            detect_tables=detect_tables,
            locale=locale,
            fast=fast,
            workers=worker_count,
        )

        document = ocr.process(
            local_file
        )

        written = ocr.export(
            document,
            job_directory,
            local_file.stem,
        )

        table_dataframe = None
        table_preview = None

        if document.tables:
            dataframes = (
                TX.html_to_dataframes(
                    document.tables[0].html
                )
            )

            if dataframes:
                table_dataframe, _ = (
                    TX.validate_table(
                        dataframes[0],
                        0,
                        locale=locale,
                    )
                )

                table_preview = (
                    [list(table_dataframe.columns)]
                    + table_dataframe.astype(
                        str
                    ).values.tolist()
                )

        made = EX.export_all(
            job_directory,
            local_file.stem,
            text=document.markdown or "",
            table_df=table_dataframe,
            summary=written.get("summary"),
            formats=chosen_formats,
            locale=locale,
        )

        output_files = files_payload(made)

        summary = written["summary"]
        table_count = summary["n_tables"]
        review_count = written.get(
            "tables_needing_review",
            0,
        )

        if mode == "auto":
            mode_note = (
                "auto preprocessing selected "
                f"{document.detected_mode}"
            )
        else:
            mode_note = (
                f"preprocessing mode {mode}"
            )

        status = (
            f"Completed {document.pages} page(s) "
            f"with {table_count} table(s); "
            f"{mode_note}."
        )

        if review_count:
            status += (
                f" {review_count} table(s) "
                "require review."
            )

        LOG.info(
            "OCR job completed job_id=%s "
            "pages=%s tables=%s",
            job_id,
            document.pages,
            table_count,
        )

        response = {
            "ok": True,
            "job_id": job_id,
            "status": status,
            "detected_mode": (
                document.detected_mode
            ),
            "text": (
                document.markdown or ""
            ),
            "table": table_preview,
            "files": output_files,
        }

        # These fields will become available after ocr.py is revised.
        if hasattr(
            document,
            "page_modes",
        ):
            response["page_modes"] = (
                document.page_modes
            )

        if hasattr(
            document,
            "mean_confidence",
        ):
            response["mean_confidence"] = (
                document.mean_confidence
            )

        if hasattr(
            document,
            "review_recommended",
        ):
            response["review_recommended"] = (
                document.review_recommended
            )

        return response

    except HTTPException:
        raise

    except Exception:
        LOG.exception(
            "OCR processing failed "
            "job_id=%s",
            job_id,
        )

        raise HTTPException(
            status_code=500,
            detail={
                "message": (
                    "OCR processing failed."
                ),
                "job_id": job_id,
            },
        )

    finally:
        try:
            await file.close()
        except Exception:
            LOG.warning(
                "Upload file could not be closed "
                "job_id=%s",
                job_id,
            )

        if (
            job_directory is not None
            and job_directory.exists()
        ):
            shutil.rmtree(
                job_directory,
                ignore_errors=True,
            )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
    )