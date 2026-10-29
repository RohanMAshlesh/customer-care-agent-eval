# Architecture

## Components

```
clients ──► API (FastAPI, stateless)        ──► PostgreSQL
            · auth binds tenant                  · events (append-only, unique dedup key)
            · ingest: validate, tokenise,        · tasks, evaluations (append-only revisions)
              redact, dedupe, correlate          · contracts, runs, release decisions
            · contracts, runs, decisions         · review items, audit log, outbox
                                                     ▲
worker (N replicas) ─────────────────────────────────┘
  · claims dirty or due tasks (FOR UPDATE SKIP LOCKED)
  · evaluate → append revision only if material content changed
  · finalise at deadlines, re-evaluate on heartbeats and late events
  · deliver signed callbacks from the outbox with backoff
```

The **control plane** holds contracts, users and keys, release decisions and the audit log. The **data plane** holds evidence, correlation and results. In this first release they share one PostgreSQL database, and every row carries `tenant_id`. The module boundaries (`ingestion`, `pipeline`, `release`, `onboarding`) are where the planes separate later, for example into a client-hosted data plane.

## Evaluation is a pure function

`evaluation.engine.evaluate(contract, task_facts, events, source_status, now, judge, adjudications)` has no I/O. The same code grades:

- stored production evidence (`pipeline.evaluate_task`),
- sandbox release runs,
- in-memory contract fixtures and dry runs (`contracts.registry.dry_run`),
- connector conformance kits.

This keeps the test oracle separate from the bot under test. The money oracle (`money.py`) is independent of any allocation code the bot calls.

## Results and revisions

A result reports each state axis separately: lifecycle and disposition, obligation completion per tender leg, evidence sufficiency per criterion, timeliness, bot behaviour per criterion, and evaluation execution. A new revision is appended only when the material content changes, so a redelivered or older webhook never creates a new verdict or review item. Revision reasons are `initial_evidence`, `new_evidence`, `finalized_at_deadline`, `late_event`, `adjudication` and `policy_correction`. Each revision has an immutable `decision_id`, and `verdict.created` or `verdict.superseded` callbacks reference it.

## Exactly-once is not claimed

Ingestion is at-least-once with deterministic deduplication. Money effects are reconciled from observed ledger and provider events keyed by logical action and provider reference. The platform never assumes an external provider executed something exactly once.

## Scaling path

The blueprint's illustrative envelope is 250M events a day, with peaks around 29k events a second. That requires the following changes, and the code is structured so each is local:

| Today | At measured scale |
| --- | --- |
| API writes events straight to PostgreSQL | Partitioned event bus in front of ingestion; PostgreSQL holds metadata and links; large transcript payloads go to encrypted object storage (`redacted_payload_ref`) |
| Worker polls `tasks.dirty` with SKIP LOCKED | Per-tenant work queues with weighted fair scheduling and concurrency budgets |
| Metrics computed from the latest revisions on request | Analytical store fed from evaluation revisions |
| Per-process rate limiter | Gateway-level, shared limits |
| Hash sampling with a fixed rate per criterion | Same mechanism; tune rates against judge budgets and rate limits |

## Security boundaries

- The authenticated key decides the tenant, and every query filters on it. Tokens derive from a per-tenant key, so no global customer identity exists.
- Roles separate authoring from approval and reviewing from releasing. Contract promotion, release decisions, exceptions and adjudications are audited.
- Tenant rules run in a restricted rule engine with bounded depth and size and three-valued logic. Tenant configuration never executes code.
- Judges get redacted conversation evidence only: no ledger records, no credentials and no tools. Citations are validated against the task's own evidence.
- The evaluator has no authority to contact end customers. Callbacks go only to the tenant's configured endpoint, signed with HMAC-SHA256 over the timestamp and body.
- In production mode, startup refuses to run with SQLite, the development tokenisation secret, or a judge provider without a key.

## Known limits of this release

- Two intent packs: missing-item refunds and service resolution (tickets, routing, SLAs, verified resolution). Account recovery with identity-takeover checks, cancellation races and billing disputes are not implemented. Connector conformance kits exist for the refund pack's sources only; ticketing and sign-in sources still need their own kits.
- Payload deletion and retention jobs, SSO, and a client-hosted data plane are not implemented.
- The baseline migration creates the schema from ORM metadata. Later migrations must be explicit and reviewed.
- Load, failover and restore targets in the blueprint are proposals to measure, not achieved guarantees.
