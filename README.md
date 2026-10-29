# Customer Care Agent Eval

This service decides whether a customer-care AI agent handled a request correctly, what happened afterwards, and whether a proposed release is acceptable. The unit it judges is a **customer task backed by evidence**, not a chat transcript with a score.

It implements the first vertical slice of the *Enterprise Customer Care Evals Blueprint* with two intent packs:

- **Missing-item refunds** (`missing_item_refund`): money movement across wallet and card, asynchronous outcomes, partial completion and policy exceptions.
- **Service resolution** (`service_resolution`): support work that is not money. It checks whether the right ticket was raised in ServiceNow, in the right queue and at the right priority, whether it was fixed within its target time, whether the customer had to come back, and whether resolution is confirmed by a system of record (for example a logistics delivery or a successful sign-in) rather than only by the customer saying so. Starter contracts cover safety complaints, damaged-item replacements and sign-in problems.

> Every amount, rule, deadline and threshold in the starter contract and the release policy is an illustrative example. None of them describes any real marketplace's policy or systems. The client's policy owner must approve real values.

## Interactive demo

`demo/` is a static web app (Vite + React) that walks through the refund journey, the edge cases, the release gate and client onboarding, using data exported from this engine. To host it on Vercel, import the repository and set the root directory to `demo`. See [demo/README.md](demo/README.md).

## What it does

| Blueprint capability | Where it lives |
| --- | --- |
| Event envelope, at-least-once ingestion, deterministic dedup, quarantine, tenant bound at auth | `src/ccevals/ingestion.py`, `schemas.py` |
| Task correlation (client task ID, derived key, logical action, conversation) | `ingestion.py` |
| Tenant-scoped tokenisation; sensitive-data detection before redaction | `privacy.py` |
| Versioned intent contracts, a restricted rule engine, policy time vs knowledge time | `contracts/`, `rules.py` |
| Contract wizard checks: built-in positive, forbidden, boundary and missing-evidence fixtures; separate author and approver | `contracts/registry.py` |
| Independent money oracle using integer minor units and largest-remainder allocation | `money.py` |
| Deterministic refund evaluators: identity, items, amounts, allocation, duplicates, limits, claims vs evidence at the time, disclosure, disposition | `evaluation/refund.py` |
| Service-resolution evaluators: ticket created in time, routing, reference shared, promises backed by records, SLA, repeat contact, verified vs customer-reported resolution | `evaluation/service.py`, `contracts/service.py` |
| Reference customer cases (refund, safety complaint, replacement, sign-in) reused by tests and the demo | `packs/reference_cases.py` |
| Independent state axes (lifecycle, obligation completion per tender leg, evidence sufficiency, timeliness, bot behaviour, execution) | `evaluation/engine.py` |
| Append-only verdict revisions: provisional, finalised at deadline, late events, adjudications | `pipeline.py` |
| Sampled, cited semantic judges that may abstain; judge outages never pass | `evaluation/semantic.py` |
| Review queue for unknowns and critical failures, each with an owner and age; contest a verdict without editing history | `api/routes/evidence.py` |
| Census-reconciled production metrics, weighted sample estimates, measurement health | `metrics.py` |
| Release gate with block, insufficient-evidence and pass outcomes, a paired cluster bootstrap, binding, 7-day validity, exceptions and supersession | `release.py`, `stats.py` |
| Client journey: nine onboarding stages, four separate readiness indicators, connector conformance kits, evaluator calibration | `onboarding.py` |
| Signed callbacks via a transactional outbox, audit log, role-based keys, environment-scoped ingest keys | `audit.py`, `security.py`, `worker.py` |

## The client journey

Onboarding moves forward through evidence gates. A client can start with a conversation audit straight away. Verified-outcome claims stay switched off until its business-evidence adapters pass conformance. Until then, outcomes are reported as `unverified`, never as complete.

| Stage | Owner | How the platform measures exit |
| --- | --- | --- |
| 1 Qualify the pilot | Support and AI leads | Attestation with a reference to the signed scope |
| 2 Establish trust | Security and platform | Attestation of the approved data flow, retention and model endpoints |
| 3 Connect observations | Integration engineer | Conversations arriving, census reconciliation, connector conformance |
| 4 Map business entities | Domain and data owners | Unmatched or ambiguous events below 1% |
| 5 Define success | Policy owner | An approved, versioned intent contract |
| 6 Validate evaluation | QA and domain experts | Passing validation-split calibration per criterion and language |
| 7 Run in shadow | Operations and release owner | Finalised production tasks with measured coverage |
| 8 Activate release gates | Release and support leads | Attestation, at least one release decision and a practised override |
| 9 Expand coverage | Joint product team | Attestation; the new configuration passes the same gates |

`GET /v1/readiness` reports four separate indicators: conversations connected, policies approved, business outcomes verifiable and evaluators calibrated. There is no combined score. Each missing item names the field, the owner and a sample case. See [docs/client-journey.md](docs/client-journey.md) for the walkthrough, including the end customer's refund journey from the blueprint.

## Quick start

Requirements: Python 3.11+ and PostgreSQL 14+ (SQLite works for local experiments and tests).

```bash
pip install -e ".[dev,postgres,judge]"
```

```bash
export CCEVALS_DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/ccevals"
```

```bash
ccevals db upgrade
```

```bash
ccevals tenant create --id acme --name "Acme Delivery" --webhook-url https://example.invalid/hooks/evals
```

```bash
ccevals serve --port 8000
```

```bash
ccevals worker
```

Then walk a tenant through the whole journey with the printed admin key:

```bash
python scripts/demo_journey.py --base-url http://localhost:8000 --admin-key cce_...
```

Or use Docker Compose: copy `.env.example` to `.env`, then run:

```bash
docker compose up --build
```

## API surface

| Endpoint | Purpose | Role |
| --- | --- | --- |
| `POST /v1/events:batch` | Accept evidence; per-event `accepted`, `duplicate`, `rejected` or `quarantined` with an error code | `ingest` |
| `GET /v1/contracts/starter` | Starter contract for the refund pack | any |
| `POST /v1/contracts:validate` | Schema errors, coverage gaps and fixture results | `policy_author` |
| `POST /v1/contracts` / `…:approve` | Save a draft; approve as a different person after reviewing the four examples | `policy_author` / `policy_owner` |
| `POST /v1/runs` | Register a version-pinned sandbox run | `evaluation_engineer` |
| `GET /v1/tasks/{id}/evaluations[/{rev}]` | State axes, evidence cutoff, required-check coverage and revision history | any |
| `GET /v1/review-items` | Unknowns and critical failures by owner, with age | any |
| `POST /v1/tasks/{id}/adjudications` | Contest or confirm a criterion; appends a revision | `reviewer` |
| `POST /v1/release-decisions` | Gate a candidate against a baseline and bind the decision | `release_owner` |
| `POST /v1/release-decisions/{id}/exceptions` | Time-limited exception shown beside the gate | `release_owner` |
| `GET /v1/metrics` | Census-reconciled cohort metrics and measurement health | any |
| `GET /v1/readiness`, `GET /v1/onboarding` | The four indicators and the nine-stage journey | any |
| `GET/POST /v1/connectors/{source}/conformance…` | Reference kit, then the adapter's results | `evaluation_engineer` |
| `POST /v1/calibration` | Evaluator vs adjudicated expert labels | `evaluation_engineer` |
| `GET /v1/audit`, `POST /v1/admin/api-keys` | Audit trail and key management | `admin` |

The interactive schema is at `/docs` once the server is running. Event types and payloads are listed in [docs/event-catalog.md](docs/event-catalog.md).

## Semantic judges

Set `CCEVALS_JUDGE_PROVIDER=anthropic`, `CCEVALS_JUDGE_API_KEY` and, optionally, `CCEVALS_JUDGE_MODEL`. The default model is `claude-opus-5-5`; pick the cheapest model that passes your calibration thresholds. Judges receive only redacted conversation evidence, have no tools, must cite event IDs from the task, and abstain when unsure. A refusal or an outage becomes `abstain`, never `pass`. The judge does not fall back to a different model automatically, because a silent model switch would invalidate its calibration. Without a judge, semantic criteria are reported as `not_evaluated`, and release gates use only the objective checks.

## Tests

```bash
pytest
```

```bash
CCEVALS_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/ccevals_test pytest
```

The suite covers the blueprint's reference journey end to end, the money and fulfilment edge cases from section 8, the policy and conversation edge cases from section 9, metamorphic invariants, contract governance, release gates, judge safety, onboarding, conformance, calibration, metrics and tenant isolation. CI runs it on SQLite and on PostgreSQL.

## What "production ready" means here

The blueprint is explicit: readiness is demonstrated for a named deployment envelope, not claimed from architecture. This repository provides the mechanisms and their tests. These items still need evidence from a real deployment before a launch gate can be signed:

- Load tests at the agreed envelope: sustained and peak ingestion, judge rate limits, backlog recovery and tenant fairness.
- Regional failover, backup and restore drills, and measured recovery point and recovery time.
- Penetration and tenant-isolation testing by the client's security team; SSO integration (keys are role-scoped API keys today).
- Retention and deletion jobs wired to the client's approved policy.
- Source-census reconciliation and checkpoint backfill against the client's real systems, which conformance kits cannot simulate.
- Independent expert calibration for each semantic criterion and language in scope.

[docs/architecture.md](docs/architecture.md) describes the components, their scaling path and these limits in more detail.
