import os
import secrets
import warnings
from pathlib import Path

ENV = os.getenv("SENTINEL_ENV", "development")
ON_VERCEL = os.getenv("VERCEL") == "1"  # serverless: no work may continue after a response
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{Path(__file__).resolve().parent.parent / 'data' / 'sentinel.db'}")
if DATABASE_URL.startswith("postgres://"):  # Heroku/Render style URL
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if ENV == "production":
        raise RuntimeError("SECRET_KEY must be set in production.")
    warnings.warn("SECRET_KEY not set; using a random key (sessions reset on restart).")
    SECRET_KEY = secrets.token_urlsafe(48)

TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "12"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true" if ENV == "production" else "false") == "true"
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "4" if ON_VERCEL else "15"))  # Vercel caps bodies at 4.5 MB
MAX_ROWS = int(os.getenv("MAX_ROWS", "250000"))
DEMO_ENABLED = os.getenv("DEMO_ENABLED", "true") == "true"
DEMO_MAX_WORKSPACES = int(os.getenv("DEMO_MAX_WORKSPACES", "40"))
DEMO_TTL_HOURS = int(os.getenv("DEMO_TTL_HOURS", "24"))
SYNC_JOBS = os.getenv("SYNC_JOBS", "true" if ON_VERCEL else "false") == "true"  # run analyses inline
# Behind a proxy that overwrites X-Forwarded-For (Vercel does), rate limits key on the real client IP.
TRUST_PROXY = os.getenv("TRUST_PROXY", "true" if ON_VERCEL else "false") == "true"
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))
