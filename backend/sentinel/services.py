"""Application services: versions, analysis runs, incidents, Fault Lab."""
from __future__ import annotations

import gzip
import hashlib
import io

import joblib
import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import utcnow
from .engine.analyze import ENGINE_VERSION, analyze, build_baseline, lab_scorecard
from .engine.faults import FAULTS, inject
from .engine.schema import CsvError, coerce, read_csv
from .models import AnalysisRun, Dataset, DatasetVersion, Incident, IncidentEvent, LabExperiment, ModelArtifact, User

SEV = ["low", "medium", "high", "critical"]


def parse_upload(raw: bytes) -> pd.DataFrame:
    if len(raw) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB:g} MB.")
    if not raw.strip():
        raise HTTPException(400, "File is empty.")
    try:
        return read_csv(raw, config.MAX_ROWS)
    except CsvError as e:
        raise HTTPException(400, str(e))


def add_version(db: Session, ds: Dataset, raw: bytes, filename: str, source: str, user: User | None) -> AnalysisRun:
    """Validate and store a CSV as the dataset's next version, and queue its analysis."""
    frame = parse_upload(raw)
    number = (db.scalar(select(DatasetVersion.number).where(DatasetVersion.dataset_id == ds.id)
                        .order_by(DatasetVersion.number.desc()).limit(1)) or 0) + 1
    v = DatasetVersion(dataset_id=ds.id, number=number, filename=filename[:255], source=source,
                       size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                       row_count=len(frame), column_count=frame.shape[1],
                       content_gz=gzip.compress(raw, compresslevel=6), uploaded_by=user.id if user else None)
    db.add(v)
    db.flush()
    run = AnalysisRun(dataset_id=ds.id, version_id=v.id, status="queued")
    db.add(run)
    db.flush()
    return run


def load_frame(db: Session, version: DatasetVersion):
    return read_csv(gzip.decompress(version.content_gz), config.MAX_ROWS)


def _dump(obj) -> bytes:
    buf = io.BytesIO()
    joblib.dump(obj, buf, compress=3)
    return buf.getvalue()


_models: dict[int, object] = {}  # artifact id -> model (artifacts are immutable)
_baselines: dict[str, dict] = {}  # file sha256 -> trained baseline (training is deterministic)


def _baseline(sha256: str, frame) -> dict:
    """Train once per distinct baseline file: every demo workspace shares one, and free-tier CPUs are slow."""
    if sha256 not in _baselines:
        if len(_baselines) >= 4:
            _baselines.clear()
        _baselines[sha256] = build_baseline(frame())
    return _baselines[sha256]


def active_models(db: Session, ds: Dataset) -> dict | None:
    """The dataset's current models, or None if they were trained by an older engine version."""
    arts = db.scalars(select(ModelArtifact).where(ModelArtifact.dataset_id == ds.id, ModelArtifact.active.is_(True))).all()
    if any(a.info.get("engine") != ENGINE_VERSION for a in arts):
        return None
    out = {}
    for a in arts:
        if a.id not in _models:
            if len(_models) >= 16:
                _models.clear()
            _models[a.id] = joblib.load(io.BytesIO(a.blob))  # only blobs this service wrote are unpickled
        out[a.kind] = _models[a.id]
    return out


def execute_run(db: Session, run: AnalysisRun) -> None:
    ds, version = db.get(Dataset, run.dataset_id), db.get(DatasetVersion, run.version_id)

    def stage(s):
        run.stage = s
        db.commit()

    stage("reading file")
    raw = load_frame(db, version)
    is_baseline = ds.baseline_version_id in (None, version.id)
    models = None if is_baseline else active_models(db, ds)
    if is_baseline or models is None:
        stage("learning baseline and training models")
        base_version = version if is_baseline else db.get(DatasetVersion, ds.baseline_version_id)
        base = _baseline(base_version.sha256, lambda: raw if is_baseline else load_frame(db, base_version))
        db.query(ModelArtifact).filter(ModelArtifact.dataset_id == ds.id).update({"active": False})
        ds.baseline_version_id = base_version.id
        ds.schema_json, ds.contract_json, ds.profile_json = base["schema"], base["contract"], base["profile"]
        ds.model_notes = base["notes"]
        for kind in ("row", "period"):
            m = base[f"{kind}_model"]
            if m is None:
                continue
            info = {"rows": base_version.row_count, "features": getattr(m, "numeric", []),
                    "context": getattr(m.res, "context", []), "segments": getattr(m, "segments", None),
                    "days": getattr(m, "n_days", None), "engine": ENGINE_VERSION}
            db.add(ModelArtifact(dataset_id=ds.id, trained_on_version_id=base_version.id, kind=kind,
                                 detector=m.detector, threshold=m.threshold, info=info, blob=_dump(m)))
        db.flush()
        models = {"row": base["row_model"], "period": base["period_model"]}
    stage("checking and scoring")
    res = analyze(raw, ds.schema_json, ds.contract_json, ds.rules_json or [], ds.profile_json,
                  models.get("row"), models.get("period"), is_baseline)
    exp = db.scalar(select(LabExperiment).where(LabExperiment.run_id == run.id))
    if exp:
        exp.scorecard = lab_scorecard(exp.truth, res)
    stage("updating incidents")
    run.results = {k: v for k, v in res.items() if not k.startswith("_")}
    run.summary = {**res["summary"], "version": version.number}
    apply_findings(db, ds, run, version, res["findings"])


def apply_findings(db: Session, ds: Dataset, run: AnalysisRun, version: DatasetVersion, findings: list) -> None:
    """Open new incidents, or record a recurrence on an existing open one (same fingerprint)."""
    open_ = {i.fingerprint: i for i in db.scalars(select(Incident).where(
        Incident.dataset_id == ds.id, Incident.status.in_(("open", "investigating"))))}
    seen = set()
    for f in findings:
        seen.add(f["fingerprint"])
        inc = open_.get(f["fingerprint"])
        if inc:
            inc.occurrences += 1
            inc.last_seen_at, inc.last_run_id = utcnow(), run.id
            inc.evidence, inc.summary, inc.title = f["evidence"], f["summary"], f["title"]
            if SEV.index(f["severity"]) > SEV.index(inc.severity):
                db.add(IncidentEvent(incident_id=inc.id, type="severity",
                                     body=f"Escalated {inc.severity} → {f['severity']} by version {version.number}."))
                inc.severity = f["severity"]
            db.add(IncidentEvent(incident_id=inc.id, type="seen", body=f"Seen again in version {version.number}."))
            continue
        inc = Incident(org_id=ds.org_id, dataset_id=ds.id, fingerprint=f["fingerprint"], kind=f["kind"],
                       title=f["title"], summary=f["summary"], severity=f["severity"], evidence=f["evidence"],
                       first_run_id=run.id, last_run_id=run.id)
        db.add(inc)
        db.flush()
        db.add(IncidentEvent(incident_id=inc.id, type="opened",
                             body=f"Opened by the analysis of version {version.number}"
                                  f"{' (baseline, self-referenced)' if run.summary and run.summary.get('baseline') else ''}."))
    for fp, inc in open_.items():
        if fp not in seen:
            db.add(IncidentEvent(incident_id=inc.id, type="not_seen",
                                 body=f"Not detected in version {version.number}. Consider resolving."))


def start_experiment(db: Session, ds: Dataset, specs: list[dict], seed: int, user: User) -> LabExperiment:
    if ds.schema_json is None:
        raise HTTPException(409, "This dataset has no baseline yet. Wait for its first analysis to finish.")
    # Corrupt the latest *real* version, never a previous experiment's output.
    base = db.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id, DatasetVersion.source != "fault_lab")
                     .order_by(DatasetVersion.number.desc()).limit(1))
    schema = ds.schema_json
    for s in specs:
        if s["type"] not in FAULTS:
            raise HTTPException(400, f"Unknown fault type {s['type']}.")
        col = s.get("column")
        known = {c["name"]: c["role"] for c in schema["columns"]}
        needs_numeric = s["type"] in ("unit_error", "spike", "sign_flip", "mean_shift", "type_corruption")
        if (needs_numeric or s["type"] in ("missing", "drop_column")) and col not in known:
            raise HTTPException(400, f"{FAULTS[s['type']]['label']} needs a column from this dataset.")
        if needs_numeric and known[col] != "numeric":
            raise HTTPException(400, f"`{col}` is not numeric.")
        if s["type"] == "delay" and not schema.get("ingest_time"):
            raise HTTPException(400, "This dataset has no ingestion timestamp, so it cannot be delayed.")
    df, _ = coerce(load_frame(db, base), schema)
    try:
        faulty, truth, _ = inject(df, schema, specs, seed)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"Could not inject: {e}")
    csv = faulty.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode()
    run = add_version(db, ds, csv, f"fault-lab-{base.number}-{seed}.csv", "fault_lab", user)
    exp = LabExperiment(org_id=ds.org_id, dataset_id=ds.id, base_version_id=base.id, version_id=run.version_id,
                        run_id=run.id, spec=specs, seed=seed, truth=truth, created_by=user.id)
    db.add(exp)
    db.flush()
    return exp
