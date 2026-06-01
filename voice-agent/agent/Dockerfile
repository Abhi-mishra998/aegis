FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev

COPY src/ ./src/

# Pre-download Silero VAD + turn-detector weights and the embedding model
# so first session has no cold-start download cost.
RUN uv run --module livekit.agents download-files
RUN uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Run ingestion at container build time IF docs are baked in; otherwise mount
# a pre-built chroma_db/ volume.
# COPY docs/ /docs/
# RUN uv run src/ingest.py /docs

CMD ["uv", "run", "src/agent.py", "start"]
