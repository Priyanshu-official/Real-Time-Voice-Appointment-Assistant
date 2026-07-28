FROM python:3.13-slim

WORKDIR /app

# Install required system packages for audio processing, postgres, and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libpq-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml ./

# Install python dependencies using uv
RUN uv pip install --system -r pyproject.toml || true

# Copy application source code
COPY app /app/app

# Expose API port
EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
