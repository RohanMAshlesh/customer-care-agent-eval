from __future__ import annotations

import os
import secrets
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ccevals.api.app import create_app
from ccevals.config import Settings, override_settings
from ccevals.contracts import registry
from ccevals.db import Base, create_all, init_engine, new_session
from ccevals.evaluation.semantic import NullJudge
from ccevals.models import Tenant
from ccevals.pipeline import process_pending
from ccevals.security import issue_key

IST = timezone(timedelta(hours=5, minutes=30))
START = datetime(2026, 10, 20, 9, 0, tzinfo=IST)
ALL_ROLES = ["ingest", "policy_author", "reviewer", "evaluation_engineer", "release_owner", "viewer"]


@pytest.fixture()
def settings():
    # Set CCEVALS_TEST_DATABASE_URL to a PostgreSQL URL to run the suite against PostgreSQL.
    url = os.environ.get("CCEVALS_TEST_DATABASE_URL", "sqlite://")
    s = replace(Settings(), database_url=url, inline_evaluation=False, rate_limit_per_minute=100000)
    override_settings(s)
    engine = init_engine(s.database_url)
    if not url.startswith("sqlite"):
        Base.metadata.drop_all(engine)
    create_all()
    return s


class Api:
    """Thin test client bound to one tenant, with keys per role."""

    def __init__(self, client: TestClient, tenant_id: str, keys: dict[str, str]):
        self.client = client
        self.tenant_id = tenant_id
        self.keys = keys

    def h(self, role: str = "member") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys[role]}"}

    def get(self, path: str, role: str = "member", **kw):
        return self.client.get(path, headers=self.h(role), **kw)

    def post(self, path: str, json=None, role: str = "member", **kw):
        return self.client.post(path, json=json, headers=self.h(role), **kw)

    def ingest(self, events: list[dict], role: str = "member"):
        response = self.post("/v1/events:batch", {"events": events}, role=role)
        assert response.status_code == 200, response.text
        return response.json()

    def process(self, now: datetime) -> int:
        with new_session() as session:
            count = process_pending(session, self.client.app.state.judge, now=now, tenant_id=self.tenant_id)
            session.commit()
        return count

    def evaluations(self, task_key: str) -> dict:
        response = self.get(f"/v1/tasks/{task_key}/evaluations")
        assert response.status_code == 200, response.text
        return response.json()


def make_tenant(client: TestClient, tenant_id: str) -> Api:
    with new_session() as session:
        session.add(Tenant(id=tenant_id, name=tenant_id, region="test", webhook_secret=secrets.token_hex(8)))
        session.flush()
        keys = {
            "member": issue_key(session, tenant_id, "member", ALL_ROLES),
            "owner": issue_key(session, tenant_id, "policy-owner", ["policy_owner", "viewer"]),
            "admin": issue_key(session, tenant_id, "admin", ["admin"]),
            "viewer": issue_key(session, tenant_id, "viewer", ["viewer"]),
            "sandbox_ingest": issue_key(session, tenant_id, "sandbox", ["ingest", "env:sandbox"]),
        }
        session.commit()
    return Api(client, tenant_id, keys)


@pytest.fixture()
def client(settings):
    app = create_app(settings, judge=NullJudge())
    with TestClient(app) as c:
        yield c


def approve_starter(api: Api, overrides: dict | None = None) -> str:
    body = registry.starter_contract()
    body.update(overrides or {})
    r = api.post("/v1/contracts", {"contract": body})
    assert r.status_code == 201, r.text
    r = api.post(f"/v1/contracts/{body['name']}/versions/{body['version']}:approve",
                 {"reviewed_fixtures": list(registry.REQUIRED_FIXTURES)}, role="owner")
    assert r.status_code == 200, r.text
    return r.json()["ref"]


def verify_all_sources(api: Api) -> None:
    for source in ("identity_service", "order_service", "wallet_ledger", "payment_adapter"):
        kit = api.get(f"/v1/connectors/{source}/conformance-kit").json()
        r = api.post(f"/v1/connectors/{source}/conformance", {"fixtures": kit["fixtures"]})
        assert r.status_code == 201 and r.json()["passed"], r.text


@pytest.fixture()
def api(client) -> Api:
    a = make_tenant(client, "tenant_a")
    approve_starter(a)
    verify_all_sources(a)
    return a
