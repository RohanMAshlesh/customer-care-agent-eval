"""Persistent domain model.

Every table that holds tenant data carries ``tenant_id`` and every query in the
service layer filters on the tenant bound at authentication. Evidence and
evaluation results are append-only: corrections create new rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ccevals.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """Stores UTC and always returns timezone-aware datetimes, including on SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not accepted")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str] = mapped_column(String(64))
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    webhook_secret: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(128))
    roles: Mapped[list[str]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Event(Base):
    """One ingested piece of evidence, normalised into the event envelope."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedup_key", name="uq_events_dedup"),
        Index("ix_events_tenant_task", "tenant_id", "task_key"),
        Index("ix_events_tenant_order", "tenant_id", "order_token"),
        Index("ix_events_tenant_action", "tenant_id", "logical_action_id"),
        Index("ix_events_tenant_type", "tenant_id", "event_type"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    event_id: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(100))
    source_event_id: Mapped[str] = mapped_column(String(200))
    dedup_key: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(20))
    region: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    source_object_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    task_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_family: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    customer_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_refs: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    logical_action_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100))
    policy_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bot_deployment_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    redacted_payload_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    signature_status: Mapped[str] = mapped_column(String(20), default="unsigned")
    evidence_authority: Mapped[str] = mapped_column(String(100))
    correlation_status: Mapped[str] = mapped_column(String(20), default="pending")
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    quarantine_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Task(Base):
    """A customer task: the primary unit of evaluation."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "task_key", name="uq_tasks_key"),
        Index("ix_tasks_due", "tenant_id", "finalized", "deadline_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    task_key: Mapped[str] = mapped_column(String(200))
    intent: Mapped[str] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(String(32))
    customer_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    order_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    conversation_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_family: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    in_census: Mapped[bool] = mapped_column(Boolean, default=False)
    has_conversation: Mapped[bool] = mapped_column(Boolean, default=False)
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Evaluation(Base):
    """An immutable result revision for a task."""

    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("task_id", "revision", name="uq_evaluation_revision"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    decision_id: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    evidence_cutoff: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    contract_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evaluator_bundle: Mapped[str] = mapped_column(String(100))
    provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSONType)
    supersedes_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Contract(Base):
    """A versioned intent contract. Approved versions are immutable."""

    __tablename__ = "contracts"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version", name="uq_contract_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    intent: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime)
    body: Mapped[dict[str, Any]] = mapped_column(JSONType)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    @property
    def ref(self) -> str:
        return f"{self.name}@v{self.version}"


class ReviewItem(Base):
    """Work queue for unknown outcomes, critical failures and contested verdicts."""

    __tablename__ = "review_items"
    __table_args__ = (Index("ix_review_open", "tenant_id", "status", "owner_role"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    reason_code: Mapped[str] = mapped_column(String(100))
    criterion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    owner_role: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="open")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Adjudication(Base):
    """A reviewer's label for one criterion of one task. Produces a new result revision."""

    __tablename__ = "adjudications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    criterion: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text)
    evidence_event_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    reviewer: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Run(Base):
    """A version-pinned sandbox evaluation of a bot deployment over scenario families."""

    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    bot_deployment_ref: Mapped[str] = mapped_column(String(200))
    contract_ref: Mapped[str] = mapped_column(String(200))
    evaluator_bundle: Mapped[str] = mapped_column(String(100))
    dataset_ref: Mapped[str] = mapped_column(String(200))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ReleaseDecision(Base):
    __tablename__ = "release_decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    candidate_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    baseline_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    decision: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="active")
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    slices: Mapped[dict[str, Any]] = mapped_column(JSONType)
    binding: Mapped[dict[str, Any]] = mapped_column(JSONType)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSONType)
    accountable_owner: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    valid_until: Mapped[datetime] = mapped_column(UTCDateTime)
    superseded_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class ReleaseException(Base):
    __tablename__ = "release_exceptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("release_decisions.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(300))
    compensating_review: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    granted_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ConformanceRun(Base):
    __tablename__ = "conformance_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    source: Mapped[str] = mapped_column(String(100))
    passed: Mapped[bool] = mapped_column(Boolean)
    results: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CalibrationReport(Base):
    __tablename__ = "calibration_reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    criterion: Mapped[str] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(32))
    evaluator_bundle: Mapped[str] = mapped_column(String(100))
    passed: Mapped[bool] = mapped_column(Boolean)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Attestation(Base):
    """Signed client evidence for manual onboarding gates (scope, data flow, rollout authority)."""

    __tablename__ = "attestations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    stage: Mapped[str] = mapped_column(String(50))
    evidence_ref: Mapped[str] = mapped_column(String(500))
    note: Mapped[str] = mapped_column(Text, default="")
    attested_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditRecord(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(300))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OutboxMessage(Base):
    """Signed callbacks to the client, delivered at least once by the worker."""

    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_pending", "status", "next_attempt_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
