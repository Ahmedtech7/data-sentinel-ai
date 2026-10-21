"""Instant workspaces: anyone can use the product without a sign-up or shared password; kept for 24 h."""
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, jobs
from .db import utcnow
from .engine.faults import inject
from .engine.generator import generate_orders
from .engine.schema import coerce, infer_schema, read_csv
from .models import Dataset, Organization, User
from .services import add_version

SEED = 7
REVENUE_RULE = {"type": "formula", "expr": "abs(revenue - quantity * unit_price * (1 - discount)) <= 0.05",
                "name": "Revenue = quantity × price × (1 − discount)"}


def _csv(df) -> bytes:
    return df.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode()


def demo_files() -> tuple[bytes, bytes]:
    """Baseline (90 days) and a following month with four realistic incidents baked in."""
    base = generate_orders(SEED, days=90)
    nxt = generate_orders(SEED, days=30, day_offset=90)
    schema = infer_schema(read_csv(_csv(base), 10**6))
    typed, _ = coerce(read_csv(_csv(nxt), 10**6), schema)
    sku = typed["sku"].value_counts().index[1]  # a best-seller, so the bad price touches many orders
    faulty, _, _ = inject(typed, schema, [
        {"type": "volume_drop", "segment": "web · EMEA", "days": ["2026-04-14", "2026-04-15"], "rate": 0.85},
        {"type": "unit_error", "columns": ["unit_price", "revenue"], "factor": 100, "rate": 1.0,
         "where": {"sku": sku}, "days": ["2026-04-22", "2026-04-22"]},
        {"type": "duplicates", "rate": 0.25, "days": ["2026-04-27", "2026-04-27"]},
        {"type": "missing", "column": "discount", "rate": 0.6, "segment": "partner · NORAM",
         "days": ["2026-04-08", "2026-04-11"]},
    ], seed=SEED)
    return _csv(base), _csv(faulty)


def create_demo_workspace(db: Session) -> User:
    if not config.DEMO_ENABLED:
        raise HTTPException(404, "New workspaces are disabled.")
    cutoff = utcnow() - timedelta(hours=config.DEMO_TTL_HOURS)
    for org in db.scalars(select(Organization).where(Organization.is_demo.is_(True),
                                                     Organization.created_at < cutoff)):
        db.delete(org)
    db.flush()
    if db.scalar(select(func.count()).where(Organization.is_demo.is_(True))) >= config.DEMO_MAX_WORKSPACES:
        raise HTTPException(429, "Too many workspaces are open right now. Please try again later.")
    tag = secrets.token_hex(3)
    org = Organization(name=f"Workspace {tag}", is_demo=True)
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"guest-{tag}-{secrets.token_hex(4)}@workspace.invalid", name="Analyst",
                password_hash=None, role="admin")
    db.add(user)
    db.flush()
    ds = Dataset(org_id=org.id, name="Meridian Retail · Order lines", created_by=user.id, rules_json=[REVENUE_RULE],
                 description="Synthetic order-line feed for a fictional retailer: 4 regions × 3 channels, "
                             "40 SKUs. Version 1 is the 90-day baseline; version 2 is the following month.")
    db.add(ds)
    db.flush()
    base, month = demo_files()
    runs = [add_version(db, ds, base, "meridian_orders_q1.csv", "demo", user),
            add_version(db, ds, month, "meridian_orders_april.csv", "demo", user)]
    db.commit()
    for r in runs:
        jobs.submit(r.id)
    return user
