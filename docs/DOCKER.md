# Running the OCR Toolkit in Docker (Docker Desktop)

Yes — this runs great in Docker Desktop. The image bundles **Tesseract, the
language packs, OpenCV, and Streamlit**, so you don't install anything on the
host except Docker Desktop itself.

> Note: you don't copy your local `ocrenv` venv into the image. A container is a
> clean environment — the Dockerfile installs the dependencies fresh from
> `requirements.txt`. Your host venv stays for local runs; Docker is separate.

---

## 1. One-time: install Docker Desktop
Download from https://www.docker.com/products/docker-desktop and start it.
Verify in a terminal:
```bash
docker --version
```

## 2. Build & run (two ways)

### A) docker compose (easiest)
From the `OCR_Toolkit/` folder (the one with `Dockerfile`):
```bash
docker compose up --build
```
Then open **http://localhost:8501** in your browser. Stop with `Ctrl+C`
(or `docker compose down`).

### B) plain docker
```bash
docker build -t ocr-toolkit .
docker run --rm -p 8501:8501 ocr-toolkit
```
Open **http://localhost:8501**.

The web UI works exactly like running `streamlit run streamlit_app.py` locally —
upload a photo/PDF, Auto preprocessing, Table mode, download text/Excel.

---

## 3. Adding more Tesseract languages
The image ships with English (`eng`) + orientation (`osd`). To add, e.g.,
Indonesian and German, edit the `Dockerfile` package list:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd \
        tesseract-ocr-ind tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*
```
Rebuild (`docker compose up --build`). The Language dropdown auto-lists whatever
is installed.

---

## 4. Processing local files / keeping outputs
`docker-compose.yml` mounts a host folder `./data` to `/app/data` in the
container. Put files in `OCR_Toolkit/data/` and they're visible inside the
container (handy for batch CLI runs). Example CLI run inside the container:
```bash
docker compose run --rm ocr \
    python ocr.py --input /app/data/scan.png --outdir /app/data/out
```
Results appear in `OCR_Toolkit/data/out/` on your host.

---

## 5. Common tweaks
- **Different host port** (e.g. 9000): change the compose line to `- "9000:8501"`
  then open http://localhost:9000.
- **Run in background**: `docker compose up -d --build` (stop with
  `docker compose down`).
- **Rebuild after code changes**: `docker compose up --build` again.
- **See logs**: `docker compose logs -f`.

---

## 6. Troubleshooting
- **Port already in use** → change the left side of the port mapping.
- **"Failed loading language 'xxx'"** → that pack isn't installed in the image;
  add `tesseract-ocr-xxx` to the Dockerfile and rebuild.
- **Slow first build** → normal; Docker downloads the base image + packages once,
  then caches them.
- **Blank page on localhost** → give Streamlit a few seconds to boot, then refresh.

---

## Why this is a good "mock server"
The container is a reproducible, isolated service: anyone with Docker Desktop can
`docker compose up` and get the identical OCR web app on `localhost:8501`, with
no Python/Tesseract setup on their machine. That's exactly the mock-server
workflow you asked for.
