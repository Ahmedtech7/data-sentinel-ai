import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="sentinel-test-")
os.environ.update(DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db"), SYNC_JOBS="true",
                  SECRET_KEY="test-secret-key-0123456789", STATIC_DIR=f"{_tmp}/no-frontend", DEMO_MAX_WORKSPACES="3")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sentinel.engine.generator import generate_orders  # noqa: E402
from sentinel.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def csv_bytes(df) -> bytes:
    return df.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode()


@pytest.fixture(scope="session")
def baseline_csv():
    return csv_bytes(generate_orders(3, days=60, base_daily=25))


@pytest.fixture(scope="session")
def next_csv():
    return csv_bytes(generate_orders(3, days=21, base_daily=25, day_offset=60))


def signup(client, email, org="Acme Analytics", password="correct-horse-battery"):
    client.cookies.clear()
    r = client.post("/api/auth/register", json={"name": "Test User", "email": email, "password": password,
                                               "organization": org})
    assert r.status_code == 200, r.text
    return r.json()
