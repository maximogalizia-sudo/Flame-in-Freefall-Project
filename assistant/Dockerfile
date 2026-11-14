# Build from the repo root:  docker build -f assistant/Dockerfile -t flame-assistant .
FROM python:3.12-slim
WORKDIR /app
COPY assistant/requirements.txt /app/assistant/requirements.txt
RUN pip install --no-cache-dir -r /app/assistant/requirements.txt
COPY flame /app/flame
COPY models /app/models
COPY data/processed /app/data/processed
COPY assistant /app/assistant
ENV ASSISTANT_TRACE_PATH=/tmp/assistant_traces.jsonl
EXPOSE 8100
CMD ["sh", "-c", "uvicorn assistant.app:app --host 0.0.0.0 --port ${PORT:-8100}"]
