# Backend API image (FastAPI). BUILD CONTEXT = REPO ROOT (it needs LC's flame/, models/ and data/processed/):
#     docker build -f Backend/Dockerfile .
# Railway: service variable RAILWAY_DOCKERFILE_PATH=Backend/Dockerfile; deploy from Backend/ with
#     railway up .. --path-as-root --service api      (uploads the repo root, filtered by /.railwayignore)
# Required env: DATABASE_URL. Optional: LLM_SERVICE_URL, LLM_SERVICE_TOKEN, ASSISTANT_API_TOKEN,
# CORS_ORIGINS (comma-separated, e.g. the Cloudflare Pages URL).
FROM python:3.12-slim

WORKDIR /app
COPY Backend/requirements.txt Backend/requirements-rag.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Query embeddings for /rag/search: fastembed (ONNX, no torch) and BAAI/bge-small-en-v1.5 baked into the image (no
# download at start). The whole API stays around 300 MB of RAM with the model loaded.
RUN pip install --no-cache-dir -r requirements-rag.txt
ENV FASTEMBED_CACHE_PATH=/app/fastembed
RUN python -c "from fastembed import TextEmbedding as T; T('BAAI/bge-small-en-v1.5')"
ENV HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false RAG_QUERY_EMBEDDINGS=on

# LC's models: package, JSON models and curated tables (flame.models resolves them relative to /app)
COPY flame ./flame
COPY models ./models
COPY data/processed ./data/processed

# Document index (built offline by RAG/build_index.py) and the pgvector schema
COPY RAG/sql ./rag/sql
COPY RAG/index ./rag/index

COPY Backend/api ./api
COPY Backend/pipeline ./pipeline
COPY Backend/sql ./sql
# The web (same files as the Cloudflare Pages site), also served by this API at /app: same origin, no CORS, and it can
# be redeployed together with the endpoints it calls.
COPY Frontend ./web

# The image is only built if LC's model tests pass inside it
COPY tests/test_flame_models.py ./tests/test_flame_models.py
RUN python -m pytest -q -p no:cacheprovider tests/test_flame_models.py && rm -rf tests

ENV PORT=8000
EXPOSE 8000
# On every start, reload LC's curated tables and the document index shipped in this image, so that /tests/search,
# /rag/search and flame.models always read the same data. If a load fails, the API still starts and logs it.
CMD ["sh", "-c", "python -m pipeline.load_curated || echo 'WARNING: curated tables not loaded'; python -m pipeline.load_rag || echo 'WARNING: rag index not loaded'; uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
