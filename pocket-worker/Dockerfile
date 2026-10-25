FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copy the worker and all vendored Pocket Option read-only adapter modules.
COPY . .
EXPOSE 10000
CMD ["sh", "-c", "uvicorn worker:app --host 0.0.0.0 --port ${PORT:-10000}"]
