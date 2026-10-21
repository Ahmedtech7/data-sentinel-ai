from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer

from .. import config, jobs
from ..engine.checks import RuleError, compile_formula
from ..models import AnalysisRun, Dataset, DatasetVersion, Incident, ModelArtifact, User
from ..security import current_user, get_db, require
from ..services import add_version

router = APIRouter(prefix="/api", tags=["datasets"])


def owned(db: Session, dataset_id: int, user: User) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if not ds or ds.org_id != user.org_id:  # 404, not 403: don't reveal other tenants' ids
        raise HTTPException(404, "Dataset not found.")
    return ds


def run_json(r: AnalysisRun | None) -> dict | None:
    return r and {"id": r.id, "version_id": r.version_id, "status": r.status, "stage": r.stage, "error": r.error,
                  "summary": r.summary, "created_at": r.created_at, "finished_at": r.finished_at}


async def _read_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "Please upload a .csv file.")
    data = await file.read(int(config.MAX_UPLOAD_MB * 1024 * 1024) + 1)
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB:g} MB.")
    return data


@router.get("/datasets")
def list_datasets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = []
    for ds in db.scalars(select(Dataset).where(Dataset.org_id == user.org_id).order_by(Dataset.id)):
        latest = db.scalar(select(AnalysisRun).where(AnalysisRun.dataset_id == ds.id)
                           .order_by(AnalysisRun.id.desc()).limit(1))
        nver = db.scalar(select(func.count()).where(DatasetVersion.dataset_id == ds.id))
        open_inc = db.scalar(select(func.count()).where(Incident.dataset_id == ds.id,
                                                        Incident.status.in_(("open", "investigating"))))
        out.append({"id": ds.id, "name": ds.name, "description": ds.description, "versions": nver,
                    "open_incidents": open_inc, "latest_run": run_json(latest), "created_at": ds.created_at})
    return out


@router.post("/datasets")
async def create_dataset(name: str = Form(..., min_length=1, max_length=120), description: str = Form("", max_length=1000),
                         file: UploadFile = File(...), user: User = Depends(require("analyst")),
                         db: Session = Depends(get_db)):
    raw = await _read_upload(file)
    ds = Dataset(org_id=user.org_id, name=name.strip(), description=description.strip(), created_by=user.id,
                 rules_json=[])
    db.add(ds)
    db.flush()
    run = add_version(db, ds, raw, file.filename, "upload", user)
    db.commit()
    jobs.submit(run.id)
    return {"id": ds.id, "run": run_json(run)}


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ds = owned(db, dataset_id, user)
    versions = db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
                          .order_by(DatasetVersion.number.desc())).all()
    runs = {r.version_id: r for r in db.scalars(select(AnalysisRun).where(AnalysisRun.dataset_id == ds.id)
                                                 .order_by(AnalysisRun.id))}
    models = db.scalars(select(ModelArtifact).where(ModelArtifact.dataset_id == ds.id)
                        .order_by(ModelArtifact.id.desc())).all()
    return {
        "id": ds.id, "name": ds.name, "description": ds.description, "created_at": ds.created_at,
        "baseline_version_id": ds.baseline_version_id, "schema": ds.schema_json,
        "contract": ds.contract_json, "rules": ds.rules_json or [], "notes": ds.model_notes or [],
        "versions": [{"id": v.id, "number": v.number, "filename": v.filename, "source": v.source,
                      "rows": v.row_count, "columns": v.column_count, "size_bytes": v.size_bytes,
                      "sha256": v.sha256, "created_at": v.created_at, "run": run_json(runs.get(v.id))}
                     for v in versions],
        "models": [{"id": m.id, "kind": m.kind, "detector": m.detector, "threshold": m.threshold,
                    "active": m.active, "trained_on_version_id": m.trained_on_version_id, "info": m.info,
                    "created_at": m.created_at} for m in models],
    }


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: int, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    db.delete(owned(db, dataset_id, user))
    db.commit()
    return {"ok": True}


@router.post("/datasets/{dataset_id}/versions")
async def upload_version(dataset_id: int, file: UploadFile = File(...), user: User = Depends(require("analyst")),
                         db: Session = Depends(get_db)):
    ds = owned(db, dataset_id, user)
    raw = await _read_upload(file)
    run = add_version(db, ds, raw, file.filename, "upload", user)
    db.commit()
    jobs.submit(run.id)
    return run_json(run)


class Baseline(BaseModel):
    version_id: int


@router.post("/datasets/{dataset_id}/baseline")
def set_baseline(dataset_id: int, body: Baseline, user: User = Depends(require("analyst")),
                 db: Session = Depends(get_db)):
    """Re-learn 'normal' from another version: retrains the models and re-infers the contract."""
    ds = owned(db, dataset_id, user)
    v = db.get(DatasetVersion, body.version_id)
    if not v or v.dataset_id != ds.id:
        raise HTTPException(404, "Version not found.")
    ds.baseline_version_id = v.id
    run = AnalysisRun(dataset_id=ds.id, version_id=v.id, status="queued")
    db.add(run)
    db.commit()
    jobs.submit(run.id)
    return run_json(run)


class Rule(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expr: str = Field(min_length=3, max_length=300)


@router.put("/datasets/{dataset_id}/rules")
def set_rules(dataset_id: int, rules: list[Rule], user: User = Depends(require("analyst")),
              db: Session = Depends(get_db)):
    ds = owned(db, dataset_id, user)
    cols = [c["name"] for c in (ds.schema_json or {}).get("columns", [])]
    for r in rules:
        try:
            compile_formula(r.expr, cols)
        except RuleError as e:
            raise HTTPException(400, f"{r.name}: {e}")
    ds.rules_json = [{"type": "formula", "name": r.name, "expr": r.expr} for r in rules]
    db.commit()
    return ds.rules_json


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.scalar(select(AnalysisRun).options(undefer(AnalysisRun.results)).where(AnalysisRun.id == run_id))
    if not run:
        raise HTTPException(404, "Run not found.")
    owned(db, run.dataset_id, user)
    v = db.get(DatasetVersion, run.version_id)
    return {**run_json(run), "results": run.results, "version": {"id": v.id, "number": v.number,
                                                                 "filename": v.filename, "source": v.source}}
