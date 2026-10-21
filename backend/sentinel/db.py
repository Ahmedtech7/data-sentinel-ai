import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


def _default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o)}")


def _clean(o):
    """NaN/inf are not valid JSON: store them as null."""
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, np.ndarray)):
        return [_clean(v) for v in (sorted(o) if isinstance(o, set) else o)]
    return o


def dumps(o) -> str:
    return json.dumps(_clean(o), default=_default, allow_nan=False)


is_sqlite = config.DATABASE_URL.startswith("sqlite")
if is_sqlite:
    Path(config.DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(config.DATABASE_URL, json_serializer=dumps, pool_pre_ping=True,
                       # Poolers (Neon/PgBouncer) can't keep per-connection prepared statements.
                       connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {"prepare_threshold": None})
if is_sqlite:
    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)
