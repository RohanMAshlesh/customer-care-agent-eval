"""Criterion results and the result vocabulary shared by all intent packs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ResultValue = Literal["pass", "fail", "abstain", "not_evaluated", "not_applicable", "unknown"]
Sufficiency = Literal["sufficient", "missing", "contradictory", "not_required"]


@dataclass
class CriterionResult:
    criterion: str
    result: ResultValue
    severity: str
    kind: Literal["objective", "semantic"]
    reason: str
    evidence: list[str] = field(default_factory=list)
    sufficiency: Sufficiency = "sufficient"
    source: Literal["rule", "judge", "adjudication"] = "rule"
    detail: dict[str, Any] = field(default_factory=dict)
    unknown_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["detail"]:
            data.pop("detail")
        if data["unknown_reason"] is None:
            data.pop("unknown_reason")
        return data


def passed(result: CriterionResult) -> bool:
    return result.result in ("pass", "not_applicable")
