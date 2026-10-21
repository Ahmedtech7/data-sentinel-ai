"""Background analysis runs.

shortcut: a single in-process worker thread with run state persisted in the DB.
Survives restarts (unfinished runs are re-queued on startup) but not multiple
app instances; move to a real queue (RQ/Celery/Arq) before scaling out.
"""
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from . import config
from .db import SessionLocal, utcnow
from .models import AnalysisRun

log = logging.getLogger("sentinel.jobs")
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")


def submit(run_id: int) -> None:
    if config.SYNC_JOBS:
        _execute(run_id)
    else:
        _pool.submit(_execute, run_id)


def _execute(run_id: int) -> None:
    from .services import execute_run  # avoid import cycle

    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        if not run or run.status not in ("queued", "running"):
            return
        run.status, run.started_at, run.stage = "running", utcnow(), "starting"
        db.commit()
        try:
            execute_run(db, run)
            run.status = "succeeded"
        except Exception as e:  # report every failure on the run rather than losing it in a thread
            db.rollback()
            log.error("run %s failed: %s", run_id, traceback.format_exc())
            run = db.get(AnalysisRun, run_id)
            msg = f"column {e} not found" if isinstance(e, KeyError) else str(e) or e.__class__.__name__
            run.status, run.error = "failed", f"{e.__class__.__name__}: {msg}"[:2000]
        run.finished_at, run.stage = utcnow(), ""
        db.commit()


def resume_pending() -> None:
    with SessionLocal() as db:
        ids = db.scalars(select(AnalysisRun.id).where(AnalysisRun.status.in_(("queued", "running")))
                         .order_by(AnalysisRun.id)).all()
        db.query(AnalysisRun).filter(AnalysisRun.id.in_(ids)).update({"status": "queued"})
        db.commit()
    for i in ids:
        submit(i)
