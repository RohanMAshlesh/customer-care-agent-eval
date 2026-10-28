"""At-least-once ingestion with deterministic deduplication and task correlation.

An acknowledgement means the event was durably written (or was already
present). It never means a task passed evaluation.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ccevals.evaluation.evidence import EventView
from ccevals.models import Event, Task, new_id, utcnow
from ccevals.privacy import protect_payload, tokenize_entities
from ccevals.schemas import BatchReceipt, EventIn, EventRejection, EventResult, validate_payload

ORDER_SCOPED = {"order.snapshot"}
CUSTOMER_SCOPED = {"identity.verified", "customer.profile"}
GLOBAL_SCOPED = {"source.heartbeat"}
TASK_CREATING = {"task.opened", "census.task_assigned"}
# Evidence that can only be joined through these keys is rejected without them.
REQUIRED_ENTITY_KEYS = {
    "identity.verified": ("customer_id",),
    "order.snapshot": ("order_id", "customer_id"),
    "customer.profile": ("customer_id",),
    "task.opened": ("customer_id", "order_id"),
    "bot.action": ("customer_id", "order_id"),
}


def dedup_key(source: str, source_event_id: str) -> str:
    return hashlib.sha256(f"{source}\x00{source_event_id}".encode()).hexdigest()


def derived_task_key(customer_token: str | None, order_token: str | None, lines: list[dict[str, Any]], intent: str) -> str:
    parts = sorted(f"{line['line_item_id']}" for line in lines)
    raw = "|".join([customer_token or "", order_token or "", ",".join(parts), intent])
    return "drv_" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def normalize(tenant_id: str, region: str, raw: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Validate one envelope and return column values for ``Event``.

    Raises ``EventRejection`` for envelope problems. Payload schema problems
    produce a quarantined record so the evidence stays visible to its owner.
    """
    try:
        envelope = EventIn.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise EventRejection("invalid_envelope", f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}") from exc
    if envelope.tenant_id is not None and envelope.tenant_id != tenant_id:
        raise EventRejection("tenant_mismatch", "tenant_id in the payload does not match the authenticated tenant")
    for key in REQUIRED_ENTITY_KEYS.get(envelope.event_type, ()):
        if not envelope.entity_refs.get(key):
            raise EventRejection("missing_entity_key", f"{envelope.event_type} requires entity_refs.{key}")

    quarantined, reason = False, None
    try:
        payload = validate_payload(envelope)
    except EventRejection as exc:
        if exc.code in ("unknown_event_type", "unsupported_schema_version", "missing_logical_action_id"):
            raise
        quarantined, reason = True, f"{exc.code}: {exc.message}"
        payload = {"_invalid": True}
    if envelope.signature_status == "invalid":
        quarantined, reason = True, "signature_invalid"

    refs = tokenize_entities(tenant_id, {k: str(v) for k, v in envelope.entity_refs.items()})
    if not quarantined:
        payload = protect_payload(tenant_id, envelope.event_type, payload)
    return {
        "tenant_id": tenant_id,
        "event_id": envelope.event_id,
        "source": envelope.source,
        "source_event_id": envelope.source_event_id,
        "dedup_key": dedup_key(envelope.source, envelope.source_event_id),
        "schema_version": envelope.schema_version,
        "region": region,
        "environment": envelope.environment,
        "occurred_at": envelope.occurred_at.astimezone(UTC),
        "observed_at": (envelope.observed_at or now).astimezone(UTC),
        "ingested_at": now,
        "source_object_version": envelope.source_object_version,
        "conversation_id": envelope.conversation_id,
        "task_key": envelope.task_id,
        "case_id": envelope.case_id,
        "trace_id": envelope.trace_id,
        "run_id": envelope.run_id,
        "scenario_family": envelope.scenario_family,
        "trial": envelope.trial,
        "order_token": refs.get("order_id"),
        "customer_token": refs.get("customer_id"),
        "entity_refs": refs,
        "logical_action_id": envelope.logical_action_id,
        "event_type": envelope.event_type,
        "policy_ref": envelope.policy_ref,
        "bot_deployment_ref": envelope.bot_deployment_ref,
        "payload": payload,
        "redacted_payload_ref": envelope.redacted_payload_ref,
        "signature_status": envelope.signature_status,
        "evidence_authority": envelope.evidence_authority or envelope.source,
        "quarantined": quarantined,
        "quarantine_reason": reason,
    }


def view_from_values(values: dict[str, Any], event_id: str) -> EventView:
    return EventView(
        id=event_id,
        event_type=values["event_type"],
        source=values["source"],
        occurred_at=values["occurred_at"],
        observed_at=values["observed_at"],
        payload=values["payload"],
        entity_refs=values["entity_refs"],
        logical_action_id=values["logical_action_id"],
        source_object_version=values["source_object_version"],
        evidence_authority=values["evidence_authority"],
        quarantined=values["quarantined"],
        customer_token=values["customer_token"],
        order_token=values["order_token"],
        conversation_id=values["conversation_id"],
    )


def view(event: Event) -> EventView:
    return EventView(
        id=event.id,
        event_type=event.event_type,
        source=event.source,
        occurred_at=event.occurred_at,
        observed_at=event.observed_at,
        payload=event.payload,
        entity_refs=event.entity_refs,
        logical_action_id=event.logical_action_id,
        source_object_version=event.source_object_version,
        evidence_authority=event.evidence_authority,
        quarantined=event.quarantined,
        customer_token=event.customer_token,
        order_token=event.order_token,
        conversation_id=event.conversation_id,
    )


def in_memory_views(tenant_id: str, raws: list[dict[str, Any]], region: str = "fixture") -> list[EventView]:
    """Normalise envelopes without persisting them (contract fixtures and dry runs)."""
    now = datetime.now(UTC)
    seen: set[str] = set()
    views = []
    for raw in raws:
        values = normalize(tenant_id, region, raw, now)
        if values["dedup_key"] in seen:
            continue
        seen.add(values["dedup_key"])
        views.append(view_from_values(values, values["event_id"]))
    return views


# ---------------------------------------------------------------------------
# Correlation


def _find_or_create_task(session: Session, event: Event) -> Task | None:
    payload = event.payload
    key = event.task_key
    if key is None and event.event_type == "task.opened":
        key = derived_task_key(event.customer_token, event.order_token, payload.get("affected_lines", []),
                               payload.get("intent", ""))
        event.task_key = key
    if key is None:
        return None
    task = session.scalar(select(Task).where(Task.tenant_id == event.tenant_id, Task.task_key == key))
    if task is None:
        if event.event_type not in TASK_CREATING:
            return None
        task = Task(
            tenant_id=event.tenant_id, task_key=key, intent=payload.get("intent", "unknown"),
            environment=event.environment, opened_at=event.occurred_at, run_id=event.run_id,
            scenario_family=event.scenario_family, trial=event.trial, conversation_ids=[],
        )
        session.add(task)
        session.flush()
    return task


def _attach(task: Task, event: Event) -> None:
    if event.event_type == "task.opened":
        task.intent = event.payload.get("intent", task.intent)
        task.market = event.payload.get("market", task.market)
        task.language = event.payload.get("language", task.language)
        task.opened_at = min(task.opened_at, event.occurred_at)
        task.customer_token = task.customer_token or event.customer_token
        task.order_token = task.order_token or event.order_token
    if event.event_type == "census.task_assigned":
        task.in_census = True
        task.market = task.market or event.payload.get("market")
    if event.conversation_id and event.conversation_id not in (task.conversation_ids or []):
        task.conversation_ids = [*(task.conversation_ids or []), event.conversation_id]
    if event.event_type == "conversation.message":
        task.has_conversation = True
    task.customer_token = task.customer_token or event.customer_token
    task.order_token = task.order_token or event.order_token
    task.dirty = True
    event.task_key = task.task_key
    event.correlation_status = "linked"


def correlate(session: Session, event: Event) -> None:
    if event.event_type in GLOBAL_SCOPED:
        event.correlation_status = "source_scoped"
        watermark = datetime.fromisoformat(event.payload["watermark"]) if not event.quarantined else None
        if watermark is not None:
            # Coverage can turn an unknown leg into verified noncompletion, so open
            # tasks observed before the watermark are re-evaluated.
            for task in session.scalars(select(Task).where(
                Task.tenant_id == event.tenant_id, Task.environment == event.environment,
                Task.finalized.is_(False), Task.opened_at <= watermark,
            )):
                task.dirty = True
        return

    task = _find_or_create_task(session, event)
    if task is None and event.logical_action_id:
        key = session.scalar(select(Event.task_key).where(
            Event.tenant_id == event.tenant_id, Event.logical_action_id == event.logical_action_id,
            Event.task_key.is_not(None)).limit(1))
        if key:
            task = session.scalar(select(Task).where(Task.tenant_id == event.tenant_id, Task.task_key == key))
    if task is None and event.conversation_id and event.event_type not in ORDER_SCOPED | CUSTOMER_SCOPED:
        candidates = [t for t in session.scalars(select(Task).where(
            Task.tenant_id == event.tenant_id, Task.environment == event.environment,
            Task.order_token == event.order_token)) if event.conversation_id in (t.conversation_ids or [])]
        if len(candidates) == 1:
            task = candidates[0]
        elif len(candidates) > 1:
            event.correlation_status = "ambiguous"
            return
    if task is not None:
        _attach(task, event)
        return

    if event.event_type in ORDER_SCOPED | CUSTOMER_SCOPED:
        event.correlation_status = "entity_scoped"
        conditions = []
        if event.event_type in ORDER_SCOPED and event.order_token:
            conditions.append(Task.order_token == event.order_token)
        if event.customer_token:
            conditions.append(Task.customer_token == event.customer_token)
        if conditions:
            for related in session.scalars(select(Task).where(
                Task.tenant_id == event.tenant_id, Task.environment == event.environment, or_(*conditions),
            )):
                related.dirty = True
        return
    event.correlation_status = "unmatched"


def ingest_batch(session: Session, tenant_id: str, region: str, raws: list[dict[str, Any]]) -> BatchReceipt:
    now = utcnow()
    results: list[EventResult] = []
    counts = {"accepted": 0, "duplicate": 0, "rejected": 0, "quarantined": 0}
    versions: set[str] = set()
    for index, raw in enumerate(raws):
        event_id = raw.get("event_id") if isinstance(raw, dict) else None
        try:
            values = normalize(tenant_id, region, raw, now)
        except EventRejection as exc:
            results.append(EventResult(index=index, event_id=event_id, status="rejected", code=exc.code, message=exc.message))
            counts["rejected"] += 1
            continue
        versions.add(values["schema_version"])
        existing = session.scalar(select(Event.id).where(Event.tenant_id == tenant_id, Event.dedup_key == values["dedup_key"]))
        if existing:
            results.append(EventResult(index=index, event_id=event_id, status="duplicate"))
            counts["duplicate"] += 1
            continue
        event = Event(id=new_id(), **values)
        try:
            with session.begin_nested():
                session.add(event)
                session.flush()
                correlate(session, event)
                session.flush()
        except IntegrityError:
            results.append(EventResult(index=index, event_id=event_id, status="duplicate"))
            counts["duplicate"] += 1
            continue
        status = "quarantined" if event.quarantined else "accepted"
        results.append(EventResult(index=index, event_id=event_id, status=status,
                                   code=event.quarantine_reason.split(":")[0] if event.quarantine_reason else None,
                                   message=event.quarantine_reason))
        counts[status] += 1
    return BatchReceipt(receipt_id=new_id(), schema_versions=sorted(versions), accepted=counts["accepted"],
                        duplicates=counts["duplicate"], rejected=counts["rejected"],
                        quarantined=counts["quarantined"], results=results)
