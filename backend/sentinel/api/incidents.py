from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from ..db import utcnow
from ..models import Dataset, Incident, IncidentEvent, User
from ..security import current_user, get_db, require

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
STATUSES = ("open", "investigating", "resolved", "dismissed")
SEVERITIES = ("low", "medium", "high", "critical")


def incident_json(i: Incident, names: dict, evidence=False) -> dict:
    d = {"id": i.id, "dataset_id": i.dataset_id, "dataset": names.get(("ds", i.dataset_id)), "kind": i.kind,
         "title": i.title, "summary": i.summary, "severity": i.severity, "status": i.status,
         "assignee": names.get(("u", i.assignee_id)) if i.assignee_id else None, "assignee_id": i.assignee_id,
         "occurrences": i.occurrences, "opened_at": i.opened_at, "last_seen_at": i.last_seen_at,
         "resolved_at": i.resolved_at, "first_run_id": i.first_run_id, "last_run_id": i.last_run_id}
    if evidence:
        d["evidence"] = i.evidence
    return d


def _names(db: Session, org_id: int) -> dict:
    n = {("ds", d.id): d.name for d in db.scalars(select(Dataset).where(Dataset.org_id == org_id))}
    n |= {("u", u.id): u.name for u in db.scalars(select(User).where(User.org_id == org_id))}
    return n


def _owned(db: Session, incident_id: int, user: User, evidence=False) -> Incident:
    q = select(Incident).where(Incident.id == incident_id)
    i = db.scalar(q.options(undefer(Incident.evidence)) if evidence else q)
    if not i or i.org_id != user.org_id:
        raise HTTPException(404, "Incident not found.")
    return i


@router.get("")
def list_incidents(status: str | None = None, severity: str | None = None, dataset_id: int | None = None,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Incident).where(Incident.org_id == user.org_id)
    if status == "active":
        q = q.where(Incident.status.in_(("open", "investigating")))
    elif status:
        q = q.where(Incident.status == status)
    if severity:
        q = q.where(Incident.severity == severity)
    if dataset_id:
        q = q.where(Incident.dataset_id == dataset_id)
    names = _names(db, user.org_id)
    return [incident_json(i, names) for i in db.scalars(q.order_by(Incident.id.desc()).limit(500))]


@router.get("/{incident_id}")
def get_incident(incident_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    i = _owned(db, incident_id, user, evidence=True)
    names = _names(db, user.org_id)
    events = db.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == i.id).order_by(IncidentEvent.id))
    return {**incident_json(i, names, evidence=True),
            "events": [{"id": e.id, "type": e.type, "body": e.body, "created_at": e.created_at,
                        "user": names.get(("u", e.user_id)) if e.user_id else None} for e in events]}


class Patch(BaseModel):
    status: str | None = Field(None, pattern="^(open|investigating|resolved|dismissed)$")
    severity: str | None = Field(None, pattern="^(low|medium|high|critical)$")
    assignee_id: int | None = None
    note: str | None = Field(None, max_length=2000)


@router.patch("/{incident_id}")
def update_incident(incident_id: int, body: Patch, user: User = Depends(require("analyst")),
                    db: Session = Depends(get_db)):
    i = _owned(db, incident_id, user)
    note = f" — {body.note.strip()}" if body.note and body.note.strip() else ""
    if body.status and body.status != i.status:
        db.add(IncidentEvent(incident_id=i.id, user_id=user.id, type="status",
                             body=f"Status {i.status} → {body.status}{note}"))
        i.status = body.status
        i.resolved_at = utcnow() if body.status in ("resolved", "dismissed") else None
    if body.severity and body.severity != i.severity:
        db.add(IncidentEvent(incident_id=i.id, user_id=user.id, type="severity",
                             body=f"Severity {i.severity} → {body.severity}"))
        i.severity = body.severity
    if "assignee_id" in body.model_fields_set and body.assignee_id != i.assignee_id:
        a = db.get(User, body.assignee_id) if body.assignee_id else None
        if body.assignee_id and (not a or a.org_id != user.org_id):
            raise HTTPException(400, "Assignee must be a member of this workspace.")
        db.add(IncidentEvent(incident_id=i.id, user_id=user.id, type="assign",
                             body=f"Assigned to {a.name}" if a else "Unassigned"))
        i.assignee_id = body.assignee_id
    db.commit()
    return get_incident(incident_id, user, db)


class Comment(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/{incident_id}/comments")
def comment(incident_id: int, body: Comment, user: User = Depends(require("analyst")), db: Session = Depends(get_db)):
    i = _owned(db, incident_id, user)
    db.add(IncidentEvent(incident_id=i.id, user_id=user.id, type="comment", body=body.body.strip()))
    db.commit()
    return get_incident(incident_id, user, db)
