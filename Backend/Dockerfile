# Backend/Dockerfile — for AWS App Runner / ECS / EC2
FROM python:3.12-slim

WORKDIR /app

# System deps for asyncpg, pandas, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Uploads are ephemeral on App Runner/EC2 unless you move to S3 later.
# Create the upload dir and run as an unprivileged user: the app never needs
# root, and a container escape should not land as uid 0.
RUN mkdir -p data/uploads \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# /health now probes the database too, so an instance with a broken
# DATABASE_URL is marked unhealthy instead of silently serving 500s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Neon DB migrate + start. App Runner injects $PORT (defaults to 8000 locally).
# NOTE: with more than one replica, run `alembic upgrade head` as a separate
# deploy step instead — concurrent migrations race on the alembic_version row.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
