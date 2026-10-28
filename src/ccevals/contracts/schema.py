"""Typed intent contracts.

A contract is the client's approved definition of success for one intent in
one scope. It is a governed object: drafts can be edited, approved versions are
immutable, and a new version is required to change any rule or tolerance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ccevals.rules import validate_rule

Severity = Literal["critical", "major", "minor"]
Disposition = Literal["resolution", "refusal", "handoff", "partial", "awaiting_customer"]
UNKNOWN_REASONS = {
    "missing_identity_event",
    "missing_order_snapshot",
    "missing_action_evidence",
    "conflicting_source_records",
    "unsupported_language",
    "source_unverified",
    "eligibility_unknown",
    "judge_unavailable",
    "outcome_evidence_unavailable",
    "ambiguous_correlation",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scope(_Strict):
    market: str
    channel: Literal["chat", "voice", "email", "messaging"]
    languages: list[str] = Field(min_length=1)
    customer_types: list[str] = ["consumer"]
    exclusions: list[str] = []


class PolicySource(_Strict):
    document: str
    effective_from: datetime
    processing_estimate_max_days: int = Field(ge=0, le=60)


class Amounts(_Strict):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    exponent: int = Field(default=2, ge=0, le=4)
    representation: Literal["integer_minor_units"] = "integer_minor_units"


class Allocation(_Strict):
    method: Literal["pro_rata_largest_remainder", "tender_priority"]
    tender_priority: list[str] = []
    fixture_ref: str

    @model_validator(mode="after")
    def _priority_needed(self) -> Allocation:
        if self.method == "tender_priority" and not self.tender_priority:
            raise ValueError("tender_priority allocation needs a tender_priority list")
        return self


class LegEvidence(_Strict):
    source: str
    tender_type: str
    completion_stage: Literal["credited", "submitted", "completed"]
    observable_stages: list[str]
    deadline_hours: int = Field(gt=0, le=24 * 60)

    @model_validator(mode="after")
    def _stage_observable(self) -> LegEvidence:
        if self.completion_stage not in self.observable_stages:
            raise ValueError(
                f"completion_stage {self.completion_stage!r} must be one of the observable stages; "
                "choose the stage this source can actually show"
            )
        return self


class EvidenceSources(_Strict):
    identity: str
    order: str
    legs: dict[str, LegEvidence] = Field(min_length=1)


class RefundActionPolicy(_Strict):
    requires_identity: bool = True
    max_auto_amount_minor: int = Field(gt=0)
    above_cap: Literal["handoff", "refuse"] = "handoff"


class SemanticCriterion(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,60}$")
    description: str = Field(min_length=10)
    severity: Severity = "major"
    sample_rate: float = Field(default=0.05, ge=0, le=1)
    risk_sample_on: list[str] = []


class Measurement(_Strict):
    census_source: str
    maturation_hours: int = Field(gt=0)
    census_completeness_min: float = Field(default=0.995, gt=0, le=1)


class Contract(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,60}$")
    version: int = Field(ge=1)
    intent: str
    effective_from: datetime
    scope: Scope
    policy: PolicySource
    entities: list[str]
    amounts: Amounts
    allocation: Allocation
    evidence: EvidenceSources
    eligibility: dict[str, Any]
    actions: dict[str, RefundActionPolicy]
    allowed_dispositions: list[Disposition]
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


def structural_problems(contract: Contract, known_fields: set[str], critical_capable: set[str]) -> list[str]:
    """Checks that pydantic cannot express: rule validity and cross-field consistency."""
    problems = [f"eligibility: {p}" for p in validate_rule(contract.eligibility, known_fields)]
    unknown_checks = set(contract.critical_checks) - critical_capable
    if unknown_checks:
        problems.append(f"critical_checks: not provided by this intent pack: {sorted(unknown_checks)}")
    if "refund" not in contract.actions:
        problems.append("actions: the refund intent needs a 'refund' action policy")
    tender_types = [leg.tender_type for leg in contract.evidence.legs.values()]
    if len(set(tender_types)) != len(tender_types):
        problems.append("evidence.legs: each tender type must map to exactly one leg")
    if contract.allocation.method == "tender_priority":
        missing = set(contract.allocation.tender_priority) ^ set(tender_types)
        if missing:
            problems.append(f"allocation.tender_priority must list exactly the leg tender types; mismatch {sorted(missing)}")
    if contract.policy.effective_from > contract.effective_from:
        problems.append("policy.effective_from is later than the contract's effective_from")
    ids = [c.id for c in contract.semantic_criteria]
    if len(set(ids)) != len(ids):
        problems.append("semantic_criteria: ids must be unique")
    return problems
