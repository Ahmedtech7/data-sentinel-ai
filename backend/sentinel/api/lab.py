import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import jobs
from ..db import utcnow
from ..engine.analyze import ARTIFACTS
from ..engine.faults import FAULTS
from ..models import AnalysisRun, Dataset, Incident, LabExperiment, User
from ..security import current_user, get_db, require
from ..services import start_experiment
from .datasets import owned, run_json

router = APIRouter(prefix="/api", tags=["lab"])


@router.get("/lab/faults")
def faults(user: User = Depends(current_user)):
    return [{"type": k, **v} for k, v in FAULTS.items()]


class FaultSpec(BaseModel):
    type: str
    column: str | None = None
    segment: str | None = None
    days: list[str] | None = Field(None, min_length=2, max_length=2)
    rate: float | None = Field(None, gt=0, le=1)
    factor: float | None = Field(None, gt=0, le=10000)
    hours: float | None = Field(None, gt=0, le=240)


class Experiment(BaseModel):
    dataset_id: int
    faults: list[FaultSpec] = Field(min_length=1, max_length=6)
    seed: int = Field(0, ge=0, le=2**31)


def experiment_json(e: LabExperiment, run: AnalysisRun | None) -> dict:
    return {"id": e.id, "dataset_id": e.dataset_id, "spec": e.spec, "seed": e.seed, "scorecard": e.scorecard,
            "run": run_json(run), "created_at": e.created_at}


@router.post("/lab/experiments")
def create(body: Experiment, user: User = Depends(require("analyst")), db: Session = Depends(get_db)):
    ds = owned(db, body.dataset_id, user)
    specs = [s.model_dump(exclude_none=True) for s in body.faults]
    exp = start_experiment(db, ds, specs, body.seed, user)
    db.commit()
    jobs.submit(exp.run_id)
    return experiment_json(exp, db.get(AnalysisRun, exp.run_id))


@router.get("/lab/experiments")
def list_experiments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    exps = db.scalars(select(LabExperiment).where(LabExperiment.org_id == user.org_id)
                      .order_by(LabExperiment.id.desc()).limit(50))
    return [experiment_json(e, db.get(AnalysisRun, e.run_id)) for e in exps]


@router.get("/lab/experiments/{exp_id}")
def get_experiment(exp_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = db.get(LabExperiment, exp_id)
    if not e or e.org_id != user.org_id:
        raise HTTPException(404, "Experiment not found.")
    return experiment_json(e, db.get(AnalysisRun, e.run_id))


@router.get("/model/report")
def model_report():  # public: aggregate evaluation on synthetic data, shown on the landing page
    return json.loads((ARTIFACTS / "report.json").read_text())


@router.get("/overview")
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = user.org_id
    incs = db.scalars(select(Incident).where(Incident.org_id == org)).all()
    active = [i for i in incs if i.status in ("open", "investigating")]
    since = utcnow() - timedelta(days=30)
    runs = db.scalars(select(AnalysisRun).join(Dataset, Dataset.id == AnalysisRun.dataset_id)
                      .where(Dataset.org_id == org).order_by(AnalysisRun.id.desc()).limit(12)).all()
    names = {d.id: d.name for d in db.scalars(select(Dataset).where(Dataset.org_id == org))}
    resolved = [i for i in incs if i.resolved_at and i.status == "resolved"]
    mttr = (sum((i.resolved_at - i.opened_at).total_seconds() for i in resolved) / len(resolved) / 3600
            if resolved else None)
    return {
        "datasets": len(names),
        "active_incidents": {s: sum(i.severity == s for i in active) for s in ("critical", "high", "medium", "low")},
        "incidents_total": len(incs),
        "opened_last_30d": sum(i.opened_at.replace(tzinfo=since.tzinfo) >= since for i in incs),
        "resolved": len(resolved), "mean_hours_to_resolve": mttr,
        "running": db.scalar(select(func.count()).select_from(AnalysisRun).join(Dataset, Dataset.id == AnalysisRun.dataset_id)
                             .where(Dataset.org_id == org, AnalysisRun.status.in_(("queued", "running")))),
        "recent_runs": [{**run_json(r), "dataset": names.get(r.dataset_id), "dataset_id": r.dataset_id} for r in runs],
    }
