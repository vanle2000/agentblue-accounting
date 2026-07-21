FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install minimal system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 agentblue && \
    useradd --uid 1000 --gid agentblue --create-home agentblue

WORKDIR /app

# Install Python dependencies first (cache-friendly layer)
COPY pyproject.toml README.md ./
COPY app/ ./app/
RUN pip install --no-cache-dir .

# Copy application code
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Ensure the app directory is importable
ENV PYTHONPATH=/app/app

# Switch to non-root user
USER agentblue

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["curl", "-f", "http://localhost:8000/api/v1/health/live"]

CMD ["uvicorn", "agentblue.main:app", "--host", "0.0.0.0", "--port", "8000"]
