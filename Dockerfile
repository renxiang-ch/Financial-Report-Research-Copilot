FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2 and sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dev]" || pip install --no-cache-dir .

# Pre-download embedding model into image (avoids cold-start delay)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# Copy source
COPY src/ src/

EXPOSE 8000

CMD ["uvicorn", "copilot.api:app", "--host", "0.0.0.0", "--port", "8000"]
