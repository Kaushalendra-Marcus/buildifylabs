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
RUN mkdir -p data/uploads

EXPOSE 8000

# Neon DB migrate + start. App Runner injects $PORT (defaults to 8000 locally).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
