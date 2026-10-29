"""Public API schemas: the event envelope and the canonical event catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SCHEMA_VERSIONS = {"1.0"}


class _Payload(BaseModel):
    model_config = ConfigDict(extra="allow")


class Line(BaseModel):
    line_item_id: str
    quantity: int = Field(ge=1)


class CensusTaskAssigned(_Payload):
    intent: str
    eligible: bool = True
    market: str | None = None


class TaskOpened(_Payload):
    intent: str
    market: str | None = None
    language: str | None = None
    affected_lines: list[Line] = []
    topic: str | None = None
    photo_provided: bool | None = None


class ConversationMessage(_Payload):
    speaker: Literal["customer", "bot", "agent", "system"]
    text: str = Field(max_length=20_000)
    language: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    masked_upstream: bool = False


class ConversationEnded(_Payload):
    reason: Literal["resolved", "customer_left", "handoff", "timeout", "awaiting_customer"]


class IdentityVerified(_Payload):
    method: str
    valid_until: datetime | None = None


class Tender(BaseModel):
    tender_id: str
    tender_type: str
    paid_minor: int = Field(ge=0)


class OrderLine(BaseModel):
    line_item_id: str
    quantity: int = Field(ge=0)
    unit_refundable_minor: int = Field(ge=0)
    refunded_quantity: int = Field(default=0, ge=0)


class OrderSnapshot(_Payload):
    status: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    placed_at: datetime | None = None
    delivered_at: datetime | None = None
    tenders: list[Tender] = Field(min_length=1)
    lines: list[OrderLine] = Field(min_length=1)


class CustomerProfile(_Payload):
    prior_refund_count_30d: int | None = None
    fraud_flag: bool | None = None


class BotAction(_Payload):
    action: Literal["refund"]
    lines: list[Line] = Field(min_length=1)
    amount_minor: int = Field(ge=0)
    legs: dict[str, int]


class ActionAttempt(_Payload):
    attempt: int = Field(ge=1)
    outcome: Literal["accepted", "timeout", "rejected", "error"]


class BotHandoff(_Payload):
    reason: str
    queue: str | None = None


class BotRefusal(_Payload):
    reason: str


class BotClaim(_Payload):
    claim_type: Literal["completion", "submission", "estimate", "eligibility", "escalation", "ticket_reference", "resolved"]
    scope: str | list[str] = "all"
    stage: Literal["submitted", "credited", "completed"] | None = None
    amount_minor: int | None = None
    days: int | None = None
    reference: str | None = None
    extracted_by: Literal["tool", "model", "reviewer"] = "tool"
    message_event_id: str | None = None


class RefundLegStatus(_Payload):
    tender_type: str
    stage: Literal["submitted", "credited", "completed", "failed", "reversed"]
    amount_minor: int = Field(ge=0)
    provider_ref: str | None = None


class RecoveryAction(_Payload):
    tender_type: str
    owner: str
    action: str


class SourceHeartbeat(_Payload):
    watermark: datetime


class TicketCreated(_Payload):
    number: str
    table: str = "incident"
    category: str
    assignment_group: str
    priority: int = Field(ge=1, le=5)
    state: str = "new"


class TicketUpdated(_Payload):
    number: str
    state: str
    assignment_group: str | None = None
    priority: int | None = Field(default=None, ge=1, le=5)


class ServiceSignal(_Payload):
    signal: str


class CustomerFeedback(_Payload):
    resolved: bool


class ToolCall(_Payload):
    tool: str
    outcome: Literal["success", "error"]
    detail: str | None = None


class ScenarioExpected(_Payload):
    expected_dispositions: list[str] = Field(min_length=1)
    slice: dict[str, str] = {}


EVENT_CATALOG: dict[str, type[_Payload]] = {
    "census.task_assigned": CensusTaskAssigned,
    "task.opened": TaskOpened,
    "conversation.message": ConversationMessage,
    "conversation.ended": ConversationEnded,
    "identity.verified": IdentityVerified,
    "order.snapshot": OrderSnapshot,
    "customer.profile": CustomerProfile,
    "bot.action": BotAction,
    "action.attempt": ActionAttempt,
    "bot.handoff": BotHandoff,
    "bot.refusal": BotRefusal,
    "bot.claim": BotClaim,
    "refund.leg_status": RefundLegStatus,
    "recovery.action": RecoveryAction,
    "source.heartbeat": SourceHeartbeat,
    "scenario.expected": ScenarioExpected,
    "ticket.created": TicketCreated,
    "ticket.updated": TicketUpdated,
    "service.signal": ServiceSignal,
    "customer.feedback": CustomerFeedback,
    "tool.call": ToolCall,
}
# Events that must carry a logical action identifier.
ACTION_EVENTS = {"bot.action", "action.attempt", "refund.leg_status"}


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=100)
    source_event_id: str = Field(min_length=1, max_length=200)
    schema_version: str = "1.0"
    environment: Literal["production", "sandbox", "conformance"] = "production"
    occurred_at: datetime
    observed_at: datetime | None = None
    source_object_version: int | None = Field(default=None, ge=0)
    conversation_id: str | None = None
    task_id: str | None = None
    case_id: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    scenario_family: str | None = None
    trial: int | None = Field(default=None, ge=1)
    entity_refs: dict[str, str | int] = {}
    logical_action_id: str | None = None
    event_type: str
    policy_ref: str | None = None
    bot_deployment_ref: str | None = None
    payload: dict[str, Any] = {}
    redacted_payload_ref: str | None = None
    signature_status: Literal["verified", "unsigned", "invalid"] = "unsigned"
    evidence_authority: str | None = None
    # Accepted only to detect mismatches: the authenticated connection decides the tenant.
    tenant_id: str | None = None

    @field_validator("occurred_at", "observed_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must include a time zone offset")
        return value


class EventRejection(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_payload(event: EventIn) -> dict[str, Any]:
    if event.schema_version not in SCHEMA_VERSIONS:
        raise EventRejection("unsupported_schema_version", f"schema_version {event.schema_version} is not supported")
    model = EVENT_CATALOG.get(event.event_type)
    if model is None:
        raise EventRejection("unknown_event_type", f"event_type {event.event_type!r} is not in the catalog")
    if event.event_type in ACTION_EVENTS and not event.logical_action_id:
        raise EventRejection("missing_logical_action_id", f"{event.event_type} requires logical_action_id")
    try:
        parsed = model.model_validate(event.payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first["loc"])
        raise EventRejection("invalid_payload", f"payload.{location}: {first['msg']}") from exc
    return parsed.model_dump(mode="json", exclude_none=True)


class EventResult(BaseModel):
    index: int
    event_id: str | None
    status: Literal["accepted", "duplicate", "rejected", "quarantined"]
    code: str | None = None
    message: str | None = None


class BatchReceipt(BaseModel):
    receipt_id: str
    schema_versions: list[str]
    accepted: int
    duplicates: int
    rejected: int
    quarantined: int
    results: list[EventResult]


class BatchIn(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=1)
