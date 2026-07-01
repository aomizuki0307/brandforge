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
# --proxy-headers + --forwarded-allow-ips='*' make uvicorn trust Render's edge
# X-Forwarded-For, so per-client rate limiting keys on the real visitor IP
# rather than lumping every request under Render's internal proxy address.
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
