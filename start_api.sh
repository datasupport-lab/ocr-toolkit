#!/bin/sh
# Start the OCR API with multiple worker processes.
# Values come from the environment so you can tune without editing the image.
#
#   API_PROCESS_WORKERS  number of Gunicorn worker processes (parallel requests)
#   API_CONTAINER_PORT   port Gunicorn binds inside the container
#
# NOTE: total simultaneous Tesseract load is roughly
#       API_PROCESS_WORKERS * OCR_MAX_WORKERS
# Watch memory, not just CPU.

set -e

WORKERS="${API_PROCESS_WORKERS:-4}"
PORT="${API_CONTAINER_PORT:-8491}"

exec gunicorn api_server:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers "${WORKERS}" \
    --bind "0.0.0.0:${PORT}" \
    --timeout 0 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
