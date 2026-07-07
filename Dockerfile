FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-login --gecos "" appuser \
 && chown -R appuser:appuser /app

RUN mkdir -p /config && chown appuser:appuser /config

# SR3: report drop directory pulled by ShowRunner at window close.
RUN mkdir -p /report && chown appuser:appuser /report

USER appuser

EXPOSE 9090

ENTRYPOINT ["python", "main.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -sf http://127.0.0.1:9090/healthz || exit 1
