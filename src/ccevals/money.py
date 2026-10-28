"""Independent money oracle.

All amounts are integers in minor units. This module is deliberately separate
from any allocation code the bot under test may call: the evaluator must not
reuse a potentially faulty function to grade itself.
"""

from __future__ import annotations

from dataclasses import dataclass


class MoneyError(ValueError):
    pass


@dataclass(frozen=True)
class TenderShare:
    tender_id: str
    tender_type: str
    paid_minor: int


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyError(f"{name} must be an integer number of minor units, got {value!r}")
    if value < 0:
        raise MoneyError(f"{name} must not be negative")
    return value


def allocate_pro_rata(amount_minor: int, tenders: list[TenderShare]) -> dict[str, int]:
    """Allocate ``amount_minor`` across tenders in proportion to what each paid.

    Uses the largest-remainder method so the legs always sum to the amount
    exactly. Ties are broken by tender order, which the contract fixes.
    """
    _require_int(amount_minor, "amount_minor")
    if not tenders:
        raise MoneyError("at least one tender is required")
    total_paid = sum(_require_int(t.paid_minor, f"tender {t.tender_id}") for t in tenders)
    if total_paid == 0:
        raise MoneyError("tenders paid nothing")
    if amount_minor > total_paid:
        raise MoneyError("refund exceeds the amount paid")

    floors: list[int] = []
    remainders: list[tuple[int, int]] = []
    for index, tender in enumerate(tenders):
        numerator = amount_minor * tender.paid_minor
        floors.append(numerator // total_paid)
        remainders.append((numerator % total_paid, -index))
    residual = amount_minor - sum(floors)
    for _, neg_index in sorted(remainders, reverse=True)[:residual]:
        floors[-neg_index] += 1
    return {tender.tender_id: floors[i] for i, tender in enumerate(tenders)}


def allocate_priority(amount_minor: int, tenders: list[TenderShare], priority: list[str]) -> dict[str, int]:
    """Refund to tender types in a fixed priority order, never above what each paid."""
    _require_int(amount_minor, "amount_minor")
    remaining = amount_minor
    result = {t.tender_id: 0 for t in tenders}
    rank = {tender_type: i for i, tender_type in enumerate(priority)}
    for tender in sorted(tenders, key=lambda t: rank.get(t.tender_type, len(priority))):
        take = min(remaining, tender.paid_minor)
        result[tender.tender_id] = take
        remaining -= take
    if remaining:
        raise MoneyError("refund exceeds the amount paid")
    return result


def allocate(method: str, amount_minor: int, tenders: list[TenderShare], priority: list[str] | None = None) -> dict[str, int]:
    if method == "pro_rata_largest_remainder":
        return allocate_pro_rata(amount_minor, tenders)
    if method == "tender_priority":
        return allocate_priority(amount_minor, tenders, priority or [])
    raise MoneyError(f"unknown allocation method {method!r}")


def format_minor(amount_minor: int, currency: str, exponent: int = 2) -> str:
    sign = "-" if amount_minor < 0 else ""
    major, minor = divmod(abs(amount_minor), 10**exponent)
    return f"{currency} {sign}{major}.{minor:0{exponent}d}"
