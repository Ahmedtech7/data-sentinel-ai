import pytest
from conftest import signup

from sentinel import config


def upload(client, csv, name="Orders"):
    r = client.post("/api/datasets", data={"name": name}, files={"file": ("orders.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def workspace(client, baseline_csv):
    me = signup(client, "owner@acme.example.com")
    ds = upload(client, baseline_csv)
    return me, ds


def test_health_and_auth_required(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    client.cookies.clear()
    assert client.get("/api/datasets").status_code == 401
    assert client.get("/api/datasets", headers={"authorization": "Bearer nonsense"}).status_code == 401


def test_register_login_logout(client):
    signup(client, "ana@example.com")
    assert client.get("/api/auth/me").json()["role"] == "admin"
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": "ana@example.com", "password": "wrong-password!"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "ANA@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 200 and "sentinel_session" in r.cookies
    assert r.headers["set-cookie"].lower().count("httponly") == 1
    assert client.post("/api/auth/register", json={"name": "x", "email": "ana@example.com", "password": "0123456789",
                                                   "organization": "y"}).status_code == 409
    assert client.post("/api/auth/register", json={"name": "x", "email": "short@example.com", "password": "short",
                                                   "organization": "y"}).status_code == 422


def test_baseline_upload_trains_models(client, workspace):
    _, ds = workspace
    assert ds["run"]["status"] == "queued"
    d = client.get(f"/api/datasets/{ds['id']}").json()
    assert d["versions"][0]["run"]["status"] == "succeeded", d["versions"][0]["run"]
    assert {m["kind"] for m in d["models"]} == {"row", "period"}
    assert d["baseline_version_id"] == d["versions"][0]["id"]
    assert d["schema"]["key"] == "order_id"


def test_new_version_with_problems_opens_incidents(client, workspace, next_csv):
    _, ds = workspace
    lines = next_csv.decode().splitlines()
    corrupted = "\n".join(lines + lines[100:160]).encode()  # 60 replayed rows
    r = client.post(f"/api/datasets/{ds['id']}/versions", files={"file": ("april.csv", corrupted, "text/csv")})
    assert r.status_code == 200
    run = client.get(f"/api/runs/{r.json()['id']}").json()
    assert run["status"] == "succeeded", run["error"]
    dup = [f for f in run["results"]["findings"] if f["kind"] == "duplicates"]
    assert dup and dup[0]["evidence"]["count"] == 60
    incidents = client.get("/api/incidents", params={"dataset_id": ds["id"]}).json()
    inc = next(i for i in incidents if i["kind"] == "duplicates")
    assert inc["status"] == "open" and inc["severity"] in ("medium", "high")

    # Same problem again: recurrence on the open incident, not a new one.
    client.post(f"/api/datasets/{ds['id']}/versions", files={"file": ("april2.csv", corrupted, "text/csv")})
    again = client.get(f"/api/incidents/{inc['id']}").json()
    assert again["occurrences"] == 2 and any(e["type"] == "seen" for e in again["events"])

    r = client.patch(f"/api/incidents/{inc['id']}", json={"status": "resolved", "note": "Upstream retry fixed"})
    assert r.json()["status"] == "resolved" and r.json()["resolved_at"]
    r = client.post(f"/api/incidents/{inc['id']}/comments", json={"body": "Confirmed with the ETL team."})
    assert r.json()["events"][-1]["body"] == "Confirmed with the ETL team."


def test_upload_validation(client, workspace, monkeypatch):
    _, ds = workspace
    url = f"/api/datasets/{ds['id']}/versions"
    assert client.post(url, files={"file": ("x.exe", b"MZ\x00\x00", "application/octet-stream")}).status_code == 400
    assert client.post(url, files={"file": ("x.csv", b"\x00\x01binary", "text/csv")}).status_code == 400
    assert client.post(url, files={"file": ("x.csv", b"", "text/csv")}).status_code == 400
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0.001)
    assert client.post(url, files={"file": ("x.csv", b"a,b\n" + b"1,2\n" * 1000, "text/csv")}).status_code == 413


def test_formula_rules_validated(client, workspace):
    _, ds = workspace
    url = f"/api/datasets/{ds['id']}/rules"
    ok = client.put(url, json=[{"name": "Revenue", "expr": "abs(revenue - quantity * unit_price * (1 - discount)) <= 0.05"}])
    assert ok.status_code == 200
    bad = client.put(url, json=[{"name": "Evil", "expr": "__import__('os').system('id') == 0"}])
    assert bad.status_code == 400


def test_fault_lab_scorecard(client, workspace):
    _, ds = workspace
    assert any(f["type"] == "volume_drop" for f in client.get("/api/lab/faults").json())
    r = client.post("/api/lab/experiments", json={"dataset_id": ds["id"], "seed": 3, "faults": [
        {"type": "unit_error", "column": "unit_price", "factor": 100, "rate": 0.01},
        {"type": "sign_flip", "column": "quantity", "rate": 0.01},
    ]})
    assert r.status_code == 200, r.text
    exp = client.get(f"/api/lab/experiments/{r.json()['id']}").json()
    assert exp["run"]["status"] == "succeeded"
    rows = {x["fault"]: x for x in exp["scorecard"]["rows"]}
    assert rows["unit_error"]["detected"] == rows["unit_error"]["injected"] > 0
    assert rows["sign_flip"]["by_rules"] == rows["sign_flip"]["injected"]
    bad = client.post("/api/lab/experiments", json={"dataset_id": ds["id"], "faults": [{"type": "spike", "column": "region"}]})
    assert bad.status_code == 400


def test_roles_and_tenant_isolation(client, workspace, baseline_csv):
    _, ds = workspace
    r = client.post("/api/auth/members", json={"name": "Vee", "email": "viewer@acme.example.com",
                                               "password": "viewer-password-1", "role": "viewer"})
    assert r.status_code == 200
    client.cookies.clear()
    client.post("/api/auth/login", json={"email": "viewer@acme.example.com", "password": "viewer-password-1"})
    assert client.get(f"/api/datasets/{ds['id']}").status_code == 200
    assert client.post(f"/api/datasets/{ds['id']}/versions",
                       files={"file": ("a.csv", baseline_csv, "text/csv")}).status_code == 403
    assert client.delete(f"/api/datasets/{ds['id']}").status_code == 403

    signup(client, "intruder@other.example.com", org="Other Corp")
    assert client.get(f"/api/datasets/{ds['id']}").status_code == 404
    assert client.get("/api/incidents").json() == []
    body = {"dataset_id": ds["id"], "faults": [{"type": "duplicates"}]}
    assert client.post("/api/lab/experiments", json=body).status_code == 404


def test_demo_workspace_and_model_report(client):
    client.cookies.clear()
    r = client.post("/api/auth/demo")
    assert r.status_code == 200 and r.json()["organization"]["is_demo"]
    datasets = client.get("/api/datasets").json()
    assert len(datasets) == 1 and datasets[0]["versions"] == 2
    assert datasets[0]["latest_run"]["status"] == "succeeded"
    titles = " | ".join(i["title"] for i in client.get("/api/incidents").json())
    assert "volume drop" in titles and "repeated values of `order_id`" in titles
    assert client.post("/api/auth/members", json={"name": "x", "email": "x@example.com", "password": "0123456789ab",
                                                  "role": "viewer"}).status_code == 403
    rep = client.get("/api/model/report").json()
    assert rep["levels"]["row"]["selected"] and rep["levels"]["period"]["shipped"]
    ov = client.get("/api/overview").json()
    assert ov["datasets"] == 1 and sum(ov["active_incidents"].values()) > 0


def test_models_from_older_engine_are_retrained(client, baseline_csv, next_csv):
    from sentinel.db import SessionLocal
    from sentinel.engine.analyze import ENGINE_VERSION
    from sentinel.models import ModelArtifact

    signup(client, "retrain@example.com", org="Retrain Co")
    ds = upload(client, baseline_csv)
    with SessionLocal() as db:
        for a in db.query(ModelArtifact).filter_by(dataset_id=ds["id"]):
            a.info = {**a.info, "engine": "old"}
        db.commit()
    r = client.post(f"/api/datasets/{ds['id']}/versions", files={"file": ("next.csv", next_csv, "text/csv")})
    assert client.get(f"/api/runs/{r.json()['id']}").json()["status"] == "succeeded"
    models = client.get(f"/api/datasets/{ds['id']}").json()["models"]
    active = [m for m in models if m["active"]]
    assert len(active) == 2 and all(m["info"]["engine"] == ENGINE_VERSION for m in active)
    assert len(models) == 4  # the outdated pair is kept, inactive, for the record


def test_generic_csv_without_timestamp(client):
    signup(client, "generic@example.com", org="Generic Co")
    rows = ["product,weight_kg,price,in_stock"] + [f"P{i % 7},{1 + (i % 5) * 0.5},{10 + i % 9},{'yes' if i % 3 else 'no'}"
                                                   for i in range(300)]
    ds = upload(client, "\n".join(rows).encode(), name="Catalog")
    d = client.get(f"/api/datasets/{ds['id']}").json()
    assert d["versions"][0]["run"]["status"] == "succeeded"
    assert any(n.startswith("Feed model not trained") for n in d["notes"])
    assert [m["kind"] for m in d["models"]] == ["row"]
    bad = "\n".join(rows[:150] + ["P1,1.5,oops,yes"] * 5).encode()
    r = client.post(f"/api/datasets/{ds['id']}/versions", files={"file": ("c.csv", bad, "text/csv")}).json()
    kinds = {f["kind"] for f in client.get(f"/api/runs/{r['id']}").json()["results"]["findings"]}
    assert "type" in kinds  # 'oops' in a numeric column is reported, not a crash
