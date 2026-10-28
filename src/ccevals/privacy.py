"""Tokenisation and redaction applied at ingestion, before evidence is stored.

Identifiers become stable tenant-scoped tokens so joins still work but no
global customer identity exists across clients. Sensitive-data detection runs
before redaction and leaves minimal structured evidence (category, speaker and
location) so the disclosure check stays observable after masking.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from ccevals.config import get_settings

ENTITY_KEYS = ("customer_id", "order_id", "account_id", "merchant_id", "courier_id")


def tokenize(tenant_id: str, kind: str, value: str) -> str:
    key = hmac.new(get_settings().tokenization_secret.encode(), tenant_id.encode(), hashlib.sha256).digest()
    digest = hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"tok_{digest[:32]}"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("otp", re.compile(r"(?i)\b(?:otp|one[- ]time (?:password|code)|verification code)\b\D{0,12}(\d{4,8})\b")),
    ("card_number", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("government_id", re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+\d{1,3}[ -]?)?\d{10}(?!\d)")),
]


def detect_and_redact(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Return redacted text and the categories found, with character offsets."""
    findings: list[dict[str, Any]] = []
    spans: list[tuple[int, int, str]] = []
    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(s < end and start < e for s, e, _ in spans):
                continue
            if category == "card_number":
                digits = re.sub(r"\D", "", match.group())
                if not 13 <= len(digits) <= 19 or not _luhn_ok(digits):
                    continue
            spans.append((start, end, category))
    spans.sort()
    out, cursor = [], 0
    for start, end, category in spans:
        out.append(text[cursor:start])
        out.append(f"[REDACTED:{category}]")
        findings.append({"category": category, "start": start, "end": end})
        cursor = end
    out.append(text[cursor:])
    return "".join(out), findings


def protect_payload(tenant_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Redact free text in conversation messages and record disclosures."""
    if event_type != "conversation.message" or "text" not in payload:
        return payload
    redacted, findings = detect_and_redact(str(payload["text"]))
    protected = dict(payload)
    protected["text"] = redacted
    if findings:
        speaker = payload.get("speaker", "unknown")
        protected["disclosures"] = [{**f, "speaker": speaker} for f in findings]
    return protected


def tokenize_entities(tenant_id: str, entity_refs: dict[str, Any]) -> dict[str, Any]:
    tokens = dict(entity_refs)
    for key in ENTITY_KEYS:
        value = entity_refs.get(key)
        if value is not None and not str(value).startswith("tok_"):
            tokens[key] = tokenize(tenant_id, key, str(value))
    return tokens
