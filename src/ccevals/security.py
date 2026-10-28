"""API keys, role-based access and tenant binding.

The authenticated key decides the tenant. Request bodies can never nominate a
different tenant. Roles separate policy authors, approvers, reviewers and
release owners so no single key can write and approve its own rules.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccevals.models import ApiKey

ROLES = {
    "admin",
    "ingest",
    "policy_author",
    "policy_owner",
    "reviewer",
    "evaluation_engineer",
    "release_owner",
    "viewer",
}
ENVIRONMENT_SCOPES = {"env:production", "env:sandbox", "env:conformance"}


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    key_id: str
    name: str
    roles: frozenset[str]

    @property
    def actor(self) -> str:
        return f"key:{self.name}"

    def has(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles

    def may_ingest(self, environment: str) -> bool:
        scopes = {r for r in self.roles if r.startswith("env:")}
        return not scopes or f"env:{environment}" in scopes


def _hash(prefix: str, secret: str) -> str:
    return hashlib.sha256(f"{prefix}.{secret}".encode()).hexdigest()


def issue_key(session: Session, tenant_id: str, name: str, roles: list[str]) -> str:
    unknown = set(roles) - ROLES - ENVIRONMENT_SCOPES
    if unknown:
        raise ValueError(f"unknown roles: {sorted(unknown)}")
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    session.add(ApiKey(tenant_id=tenant_id, name=name, prefix=prefix, key_hash=_hash(prefix, secret), roles=sorted(roles)))
    return f"cce_{prefix}_{secret}"


def authenticate(session: Session, token: str) -> Principal | None:
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != "cce":
        return None
    _, prefix, secret = parts
    key = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None or key.revoked_at is not None:
        return None
    if not hmac.compare_digest(key.key_hash, _hash(prefix, secret)):
        return None
    return Principal(key.tenant_id, key.id, key.name, frozenset(key.roles))


class RateLimiter:
    """Per-key token bucket. Per process; put a shared limiter at the gateway for multi-replica deployments."""

    def __init__(self, per_minute: int) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(per_minute)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < cost:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - cost, now)
            return True
