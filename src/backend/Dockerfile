FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/opt/cache \
    HOME=/home/app

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt \
    && python -m copilot download-runtime \
    && useradd --create-home --home-dir /home/app --uid 10001 app \
    && chmod -R a+rX /opt/cache

COPY backend/app ./app
COPY backend/samples ./samples
COPY frontend ./frontend

ENV COPILOT_SKIP_CLI_DOWNLOAD=1
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
