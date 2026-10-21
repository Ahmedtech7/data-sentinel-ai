# ---- build the React app
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- API + static files in one small image
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    SENTINEL_ENV=production STATIC_DIR=/app/static PORT=8000
WORKDIR /app/backend
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/static
RUN useradd --create-home --uid 10001 sentinel && mkdir -p data && chown -R sentinel /app
USER sentinel
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health')"
# Migrations run on startup (sentinel.main.lifespan). Proxy headers: the platform terminates TLS.
CMD ["sh", "-c", "exec uvicorn sentinel.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
