from __future__ import annotations

import pytest

from ccevals.money import MoneyError, TenderShare, allocate_priority, allocate_pro_rata
from ccevals.privacy import detect_and_redact
from ccevals.rules import evaluate_rule, validate_rule
from ccevals.stats import (
    cohen_kappa,
    episodes_for_bound,
    paired_cluster_bootstrap,
    wilson,
    zero_failure_upper_bound,
)


def test_blueprint_allocation_example():
    tenders = [TenderShare("w", "wallet", 30000), TenderShare("c", "card", 90000)]
    assert allocate_pro_rata(24000, tenders) == {"w": 6000, "c": 18000}


def test_largest_remainder_conserves_value():
    tenders = [TenderShare("a", "wallet", 100), TenderShare("b", "card", 100), TenderShare("c", "upi", 100)]
    legs = allocate_pro_rata(100, tenders)
    assert legs == {"a": 34, "b": 33, "c": 33}  # tie goes to the first tender in contract order


def test_money_guards():
    with pytest.raises(MoneyError):
        allocate_pro_rata(10.5, [TenderShare("a", "card", 100)])  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        allocate_pro_rata(200, [TenderShare("a", "card", 100)])
    assert allocate_priority(150, [TenderShare("w", "wallet", 100), TenderShare("c", "card", 100)], ["wallet", "card"]) \
        == {"w": 100, "c": 50}


def test_rules_use_three_valued_logic():
    rule = {"all": [{"field": "order.status", "op": "==", "value": "delivered"},
                    {"field": "hours_since_delivery", "op": "<=", "value": 48}]}
    assert evaluate_rule(rule, {"order": {"status": "delivered"}, "hours_since_delivery": 3}) is True
    assert evaluate_rule(rule, {"order": {"status": "cancelled"}}) is False  # decided despite missing field
    assert evaluate_rule(rule, {"order": {"status": "delivered"}}) is None  # never invented
    assert validate_rule({"all": [{"field": "x", "op": "eval", "value": "__import__('os')"}]})


def test_zero_failure_bounds_match_blueprint():
    assert abs(zero_failure_upper_bound(300) - 0.00994) < 0.00002
    assert episodes_for_bound(0.001) == 2995


def test_bootstrap_and_intervals():
    same = {f"f{i}": [1.0, 1.0, 0.0] for i in range(20)}
    cmp = paired_cluster_bootstrap(same, same, resamples=200)
    assert cmp.difference == 0 and cmp.ci_low == 0 and cmp.ci_high == 0
    lo, hi = wilson(0, 100)
    assert lo == 0 and 0.03 < hi < 0.04
    assert cohen_kappa(["pass", "fail"], ["pass", "fail"]) == 1.0


def test_redaction_records_category_not_value():
    text, findings = detect_and_redact("card 4111 1111 1111 1111, mail a@b.co, otp is 482913, call 9876543210")
    assert "4111" not in text and "a@b.co" not in text and "482913" not in text and "9876543210" not in text
    assert {f["category"] for f in findings} == {"card_number", "email", "otp", "phone"}
    clean, none = detect_and_redact("order 1234567 for INR 240")
    assert clean == "order 1234567 for INR 240" and none == []
