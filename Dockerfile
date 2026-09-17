# ============================================================================
#  OCR Toolkit — Docker image (Tesseract + OpenCV + Streamlit)
#  Works with Docker Desktop. Builds a self-contained mock-server you can run
#  without installing Tesseract/Python on the host.
# ============================================================================
FROM python:3.12-slim

# --- system deps: Tesseract engine + language packs + libs OpenCV/PyMuPDF need ---
# tesseract-ocr-eng is bundled; add more (e.g. tesseract-ocr-ind) as you need.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-osd \
        libgl1 \
        libglib2.0-0 \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Tesseract data lives here in Debian slim images.
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app

# --- python deps first (better layer caching) ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- app source ---
COPY src/ ./src/

EXPOSE 8501

# Streamlit web UI. --server.address 0.0.0.0 so Docker can expose it.
WORKDIR /app/src
CMD ["streamlit", "run", "streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
