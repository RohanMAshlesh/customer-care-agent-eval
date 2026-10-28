"""Restricted rule engine for tenant-authored eligibility and constraint rules.

Tenant configuration never executes application code. Rules are a small JSON
tree of ``all`` / ``any`` / ``not`` nodes and field comparisons, evaluated with
three-valued logic: a missing field yields ``None`` (unknown) instead of a
guessed answer.

Example::

    {"all": [
        {"field": "order.status", "op": "in", "value": ["delivered"]},
        {"field": "hours_since_delivery", "op": "<=", "value": 48}
    ]}
"""

from __future__ import annotations

from typing import Any

MAX_DEPTH = 8
MAX_NODES = 200
OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "not_in", "exists"}


class RuleError(ValueError):
    pass


_MISSING = object()


def _lookup(facts: dict[str, Any], path: str) -> Any:
    current: Any = facts
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def validate_rule(rule: Any, known_fields: set[str] | None = None) -> list[str]:
    """Return a list of problems; empty means the rule is well formed."""
    problems: list[str] = []
    count = 0

    def walk(node: Any, depth: int, where: str) -> None:
        nonlocal count
        count += 1
        if count > MAX_NODES:
            problems.append("rule has too many nodes")
            return
        if depth > MAX_DEPTH:
            problems.append(f"{where}: rule nested too deeply")
            return
        if not isinstance(node, dict) or len(node) == 0:
            problems.append(f"{where}: each rule node must be a non-empty object")
            return
        if "all" in node or "any" in node:
            key = "all" if "all" in node else "any"
            children = node[key]
            if set(node) != {key} or not isinstance(children, list) or not children:
                problems.append(f"{where}: '{key}' must be the only key and hold a non-empty list")
                return
            for i, child in enumerate(children):
                walk(child, depth + 1, f"{where}.{key}[{i}]")
            return
        if "not" in node:
            if set(node) != {"not"}:
                problems.append(f"{where}: 'not' must be the only key")
                return
            walk(node["not"], depth + 1, f"{where}.not")
            return
        if set(node) - {"field", "op", "value"} or "field" not in node or "op" not in node:
            problems.append(f"{where}: comparisons need 'field', 'op' and optionally 'value'")
            return
        if node["op"] not in OPS:
            problems.append(f"{where}: unsupported operator {node['op']!r}")
        if node["op"] != "exists" and "value" not in node:
            problems.append(f"{where}: operator {node['op']!r} needs a value")
        if node["op"] in ("in", "not_in") and not isinstance(node.get("value"), list):
            problems.append(f"{where}: operator {node['op']!r} needs a list value")
        if known_fields is not None and node["field"] not in known_fields:
            problems.append(f"{where}: unknown field {node['field']!r}")

    walk(rule, 0, "rule")
    return problems


def evaluate_rule(rule: dict[str, Any], facts: dict[str, Any]) -> bool | None:
    """Evaluate with Kleene logic. ``None`` means the facts cannot decide."""
    if "all" in rule:
        results = [evaluate_rule(child, facts) for child in rule["all"]]
        if any(r is False for r in results):
            return False
        return None if any(r is None for r in results) else True
    if "any" in rule:
        results = [evaluate_rule(child, facts) for child in rule["any"]]
        if any(r is True for r in results):
            return True
        return None if any(r is None for r in results) else False
    if "not" in rule:
        inner = evaluate_rule(rule["not"], facts)
        return None if inner is None else not inner

    value = _lookup(facts, rule["field"])
    op = rule["op"]
    if op == "exists":
        return value is not _MISSING and value is not None
    if value is _MISSING or value is None:
        return None
    expected = rule.get("value")
    try:
        if op == "==":
            return value == expected
        if op == "!=":
            return value != expected
        if op == "in":
            return value in expected
        if op == "not_in":
            return value not in expected
        if op == "<":
            return value < expected
        if op == "<=":
            return value <= expected
        if op == ">":
            return value > expected
        if op == ">=":
            return value >= expected
    except TypeError:
        return None
    raise RuleError(f"unsupported operator {op!r}")
