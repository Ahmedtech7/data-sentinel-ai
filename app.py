# Vercel entrypoint: the application lives in backend/sentinel.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from sentinel.main import app  # noqa: E402,F401
