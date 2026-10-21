import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import config
from .db import SessionLocal, utcnow
from .models import User

COOKIE = "sentinel_session"
ROLES = ("viewer", "analyst", "admin")
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except ValueError:
        return False


def make_token(user: User) -> str:
    now = utcnow()
    return jwt.encode({"sub": str(user.id), "org": user.org_id, "iat": now,
                       "exp": now + timedelta(hours=config.TOKEN_HOURS)}, config.SECRET_KEY, algorithm="HS256")


def get_db():
    with SessionLocal() as db:
        yield db


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
    if not token:
        raise HTTPException(401, "Not signed in.")
    try:
        claims = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired. Please sign in again.")
    user = db.get(User, int(claims["sub"]))
    if not user or user.org_id != claims.get("org"):
        raise HTTPException(401, "Session is no longer valid.")
    return user


def require(role: str):
    """Dependency: the user's role must be at least `role`."""
    def check(user: User = Depends(current_user)) -> User:
        if ROLES.index(user.role) < ROLES.index(role):
            raise HTTPException(403, f"This action needs the {role} role.")
        return user
    return check


class RateLimiter:
    """In-process sliding window. shortcut: per-process memory; use Redis when running >1 instance."""

    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds, self.hits = limit, seconds, defaultdict(deque)

    def __call__(self, request: Request):
        key = request.client.host if request.client else "?"
        q, now = self.hits[key], time.monotonic()
        while q and now - q[0] > self.seconds:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(429, "Too many attempts. Please wait a moment and try again.")
        q.append(now)
