# Backend API image (FastAPI). BUILD CONTEXT = REPO ROOT (it needs LC's flame/, models/ and data/processed/):
#     docker build -f Backend/Dockerfile .
# Railway: service variable RAILWAY_DOCKERFILE_PATH=Backend/Dockerfile; deploy from Backend/ with
#     railway up .. --path-as-root --service api      (uploads the repo root, filtered by /.railwayignore)
# Required env: DATABASE_URL. Optional: LLM_SERVICE_URL, LLM_SERVICE_TOKEN, ASSISTANT_API_TOKEN,
# CORS_ORIGINS (comma-separated, e.g. the Cloudflare Pages URL).
FROM python:3.12-slim

WORKDIR /app
COPY Backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# LC's models: package, JSON models and curated tables (flame.models resolves them relative to /app)
COPY flame ./flame
COPY models ./models
COPY data/processed ./data/processed

COPY Backend/api ./api
COPY Backend/pipeline ./pipeline
COPY Backend/sql ./sql

# The image is only built if LC's model tests pass inside it
COPY tests/test_flame_models.py ./tests/test_flame_models.py
RUN python -m pytest -q -p no:cacheprovider tests/test_flame_models.py && rm -rf tests

ENV PORT=8000
EXPOSE 8000
# On every start, reload LC's curated tables from the parquet files shipped in this image, so that
# /tests/search and flame.models always read the same data. If the load fails, the API still starts.
CMD ["sh", "-c", "python -m pipeline.load_curated || echo 'WARNING: curated tables not loaded'; uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
