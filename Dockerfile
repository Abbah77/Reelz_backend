# ── Reelz Backend — Production Dockerfile ────────────────────────────────────
# Multi-stage build: lean final image, no dev tools
# Run: docker build -t reelz-backend . && docker run -p 8000:8000 --env-file .env reelz-backend

FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for lxml, cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt1-dev libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Final image ───────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages
COPY --from=builder /install /usr/local

# Runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends libxml2 libxslt1.1 && \
    rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN useradd -m -u 1001 reelz
USER reelz

# Copy source
COPY --chown=reelz:reelz . .

EXPOSE 8000

# Uvicorn with 2 workers (scale horizontally with multiple containers)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
