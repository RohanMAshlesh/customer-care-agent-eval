"""Service-resolution contracts: support work that is not money movement.

Many support requests are resolved in other systems: an incident in ServiceNow,
a replacement shipped by logistics, a successful sign-in recorded by the auth
service. The contract states which downstream work proves resolution, how it
must be routed, and by when.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from ccevals.contracts.schema import UNKNOWN_REASONS, Measurement, Scope, SemanticCriterion, _Strict

SERVICE_PACK_ID = "service_resolution"
SERVICE_CRITERIA: dict[str, str] = {
    "ticket_created": "critical",
    "promises_kept": "critical",
    "no_sensitive_disclosure": "critical",
    "ticket_routed_correctly": "major",
    "reference_shared": "major",
    "sla_met": "major",
    "no_repeat_contact": "major",
    "correct_disposition": "major",
}


class ServicePolicy(_Strict):
    document: str
    effective_from: datetime


class TicketRequirement(_Strict):
    system: str = Field(description="Evidence source that records tickets, for example servicenow")
    category: str
    assignment_group: str
    max_priority: int = Field(ge=1, le=5, description="1 is most urgent; the ticket must be at least this urgent")
    create_within_minutes: int = Field(gt=0, le=24 * 60)
    resolve_within_hours: int = Field(gt=0, le=24 * 60)
    share_reference: bool = True


class ResolutionSignal(_Strict):
    source: str
    signal: str = Field(description="Event name that proves the customer's goal was met, for example login_succeeded")
    within_hours: int = Field(gt=0, le=24 * 60)


class ServiceContract(_Strict):
    pack: Literal["service_resolution"] = "service_resolution"
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,60}$")
    version: int = Field(ge=1)
    intent: str
    effective_from: datetime
    scope: Scope
    policy: ServicePolicy
    expected_dispositions: list[Literal["resolution", "handoff", "refusal"]] = Field(min_length=1)
    ticket: TicketRequirement | None = None
    resolution: ResolutionSignal | None = None
    repeat_contact_window_hours: int = Field(default=72, gt=0)
    critical_checks: list[str]
    semantic_criteria: list[SemanticCriterion] = []
    review_owner: str
    unknown_owners: dict[str, str] = {}
    missing_evidence: Literal["unknown"] = "unknown"
    late_event_action: Literal["append_verdict_revision"] = "append_verdict_revision"
    measurement: Measurement

    @field_validator("effective_from")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("effective_from must include a time zone")
        return value

    @field_validator("unknown_owners")
    @classmethod
    def _known_reasons(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = set(value) - UNKNOWN_REASONS
        if unknown:
            raise ValueError(f"unknown reason codes: {sorted(unknown)}")
        return value

    @property
    def ref(self) -> str:
        return f"{self.name}@v{self.version}"

    def owner_for(self, reason_code: str) -> str:
        return self.unknown_owners.get(reason_code, self.review_owner)


def service_problems(contract: ServiceContract) -> list[str]:
    problems = []
    unknown = set(contract.critical_checks) - set(SERVICE_CRITERIA)
    if unknown:
        problems.append(f"critical_checks: not provided by the service pack: {sorted(unknown)}")
    if contract.ticket is None and contract.resolution is None:
        problems.append("a service contract needs a ticket requirement, a resolution signal, or both")
    if contract.ticket is None and {"ticket_created", "ticket_routed_correctly", "sla_met"} & set(contract.critical_checks):
        problems.append("ticket checks are marked critical but the contract requires no ticket")
    if contract.policy.effective_from > contract.effective_from:
        problems.append("policy.effective_from is later than the contract's effective_from")
    return problems


def is_service_body(body: dict[str, Any]) -> bool:
    return body.get("pack") == SERVICE_PACK_ID
