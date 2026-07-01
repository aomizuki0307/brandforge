# BrandForge web service — FastAPI + Genblaze on Backblaze B2.
FROM python:3.11-slim

# Faster, quieter, reproducible Python in a container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install pinned runtime deps first so the layer caches across code changes.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Application code + web assets only (see .dockerignore for exclusions).
COPY app ./app
COPY templates ./templates
COPY static ./static

# Run unprivileged.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Render (and most PaaS) inject $PORT; default to 8000 locally. Use the app
# factory so import has no side effects until the server builds the app.
# NB: we deliberately do NOT pass --forwarded-allow-ips='*'. Trusting the
# client-supplied X-Forwarded-For would let an attacker rotate that header to
# dodge the per-IP rate limit, so the limiter instead keys on the (unspoofable)
# proxy peer address — i.e. it acts as a global service-level cap that bounds
# billable-endpoint abuse. See the rate-limit note in app/main.py.
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
