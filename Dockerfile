# Backend FastAPI + model IndoSBERT (CPU). Image ±2 GB; model diunduh ke volume saat pertama jalan.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models
WORKDIR /app

# torch versi CPU (jauh lebih kecil dari versi CUDA; VPS tidak punya GPU)
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt requirements-ml.txt ./
RUN pip install -r requirements.txt -r requirements-ml.txt

COPY kbli ./kbli
COPY api ./api
COPY pipeline ./pipeline
COPY eval ./eval
COPY db ./db

EXPOSE 8000
# 1 worker: tiap worker memuat model sendiri (±2–4 GB RAM). Naikkan hanya kalau RAM cukup.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "20"]
