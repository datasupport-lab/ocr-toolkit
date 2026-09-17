# OCR Toolkit — Tesseract OCR (web UI + Docker)

Upload a photo/PDF, run Tesseract OCR, get text + tables (Excel). Includes:
- **Auto preprocessing** (picks the best mode from the uploaded file) + Manual override
- **Table mode** (reproduce a table exactly as in the image/PDF)
- **Streamlit web interface** and a **Docker** setup for Docker Desktop

## Run locally
```bash
pip install -r requirements.txt      # + install the Tesseract system binary
cd src && streamlit run streamlit_app.py
```

## Run in Docker (Docker Desktop)
```bash
docker compose up --build            # then open http://localhost:8501
```
See docs/DOCKER.md for details, adding languages, and file mounting.

## Layout
```
OCR_Toolkit/
├── Dockerfile · docker-compose.yml · .dockerignore · requirements.txt
├── src/  streamlit_app.py · ocr.py · preprocess.py · table_cpu.py
│          table_format.py · table_export.py
└── docs/ DOCKER.md
```

## Preprocessing
- **Auto (default):** inspects the file → document / photo / lowcontrast / inverted.
- **Manual:** choose one explicitly.
Tesseract path: set `TESSERACT_CMD` / `TESSDATA_PREFIX` env vars if not on PATH.
