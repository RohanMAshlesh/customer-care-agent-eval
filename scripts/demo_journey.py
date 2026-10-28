"""Walk a fresh tenant through the client journey against a running API.

Usage:
    python scripts/demo_journey.py --base-url http://localhost:8000 --admin-key cce_...

It issues role-scoped keys, approves the starter contract with a separate
policy owner, passes connector conformance with the reference kits, ingests the
blueprint's INR 1200 refund journey, and prints readiness and the task result.
Run a worker (``ccevals worker``) or enable inline evaluation so results appear.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta

import httpx

from ccevals.contracts.registry import REQUIRED_FIXTURES, starter_contract
from ccevals.packs.scenarios import ScenarioBuilder, happy_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--admin-key", required=True)
    parser.add_argument("--wait-seconds", type=float, default=20)
    args = parser.parse_args()

    http = httpx.Client(base_url=args.base_url, timeout=60)

    def call(method: str, path: str, key: str, body=None):
        r = http.request(method, path, json=body, headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text}")
        return r.json()

    def key(name: str, roles: list[str]) -> str:
        return call("POST", "/v1/admin/api-keys", args.admin_key, {"name": name, "roles": roles})["api_key"]

    author = key("policy-author", ["policy_author", "viewer"])
    owner = key("policy-owner", ["policy_owner", "viewer"])
    engineer = key("evaluation-engineer", ["evaluation_engineer", "ingest", "viewer"])

    contract = starter_contract()
    call("POST", "/v1/contracts", author, {"contract": contract})
    approved = call("POST", f"/v1/contracts/{contract['name']}/versions/{contract['version']}:approve", owner,
                    {"reviewed_fixtures": list(REQUIRED_FIXTURES)})
    print("approved", approved["ref"])

    for source in ("identity_service", "order_service", "wallet_ledger", "payment_adapter"):
        kit = call("GET", f"/v1/connectors/{source}/conformance-kit", engineer)
        report = call("POST", f"/v1/connectors/{source}/conformance", engineer, {"fixtures": kit["fixtures"]})
        print("conformance", source, "passed" if report["passed"] else "FAILED")

    start = datetime.now(UTC) - timedelta(days=3)
    prefix = f"demo-{int(time.time())}"
    journey = happy_path(ScenarioBuilder(prefix, start))
    receipt = call("POST", "/v1/events:batch", engineer, {"events": journey.events})
    print("ingested", {k: receipt[k] for k in ("accepted", "duplicates", "rejected", "quarantined")})

    deadline = time.time() + args.wait_seconds
    result = None
    while time.time() < deadline:
        r = http.get(f"/v1/tasks/{journey.task_id}/evaluations", headers={"Authorization": f"Bearer {engineer}"})
        if r.status_code == 200 and r.json()["current"]:
            result = r.json()
            break
        time.sleep(1)
    if result is None:
        print("no evaluation yet: is a worker running?")
    else:
        current = result["current"]["result"]
        print("business outcome:", current["business_outcome"])
        print("disposition:", current["axes"]["disposition"], "correct:", current["disposition_correct"])
        print("criteria:", json.dumps({k: v["result"] for k, v in current["axes"]["bot_behaviour"].items()}, indent=2))
    print("readiness:", json.dumps({k: v["complete"] for k, v in call("GET", "/v1/readiness", engineer)["indicators"].items()}))


if __name__ == "__main__":
    main()
