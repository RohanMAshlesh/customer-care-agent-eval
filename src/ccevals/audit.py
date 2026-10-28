"""Audit records and signed client callbacks (transactional outbox)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from sqlalchemy.orm import Session

from ccevals.models import AuditRecord, OutboxMessage


def audit(session: Session, tenant_id: str, actor: str, action: str, target: str, **detail: Any) -> None:
    session.add(AuditRecord(tenant_id=tenant_id, actor=actor, action=action, target=target, detail=detail))


def enqueue_callback(session: Session, tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Written in the same transaction as the state change it announces."""
    session.add(OutboxMessage(tenant_id=tenant_id, event_type=event_type, payload=payload))


def sign(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    mac = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-CCEvals-Timestamp": ts, "X-CCEvals-Signature": f"v1={mac}"}


def verify(secret: str, body: bytes, headers: dict[str, str], tolerance_s: int = 300) -> bool:
    """Helper for clients: check a callback's signature and freshness."""
    ts = headers.get("X-CCEvals-Timestamp", "")
    sig = headers.get("X-CCEvals-Signature", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > tolerance_s:
        return False
    expected = sign(secret, body, int(ts))["X-CCEvals-Signature"]
    return hmac.compare_digest(expected, sig)


def callback_body(message: OutboxMessage) -> bytes:
    return json.dumps({"id": message.id, "type": message.event_type, "data": message.payload},
                      sort_keys=True, separators=(",", ":")).encode()
