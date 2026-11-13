# Backend API image (FastAPI). Works on any container host (Render, Railway, Fly.io...).
# Required env: DATABASE_URL. Optional: RAG_SERVICE_URL, RAG_SERVICE_TOKEN,
# LLM_SERVICE_URL, LLM_SERVICE_TOKEN, CORS_ORIGINS (comma-separated, e.g. the Cloudflare Pages URL).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY pipeline ./pipeline
COPY sql ./sql

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
