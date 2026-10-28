"""Background worker: evaluation, deadline finalisation and callback delivery."""

from __future__ import annotations

import logging
import signal
import time
from datetime import timedelta

import httpx
from sqlalchemy import select

from ccevals.audit import callback_body, sign
from ccevals.config import get_settings
from ccevals.db import is_postgres, session_scope
from ccevals.evaluation.semantic import Judge, build_judge
from ccevals.models import OutboxMessage, Tenant, utcnow
from ccevals.pipeline import process_pending

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 12


def deliver_callbacks(client: httpx.Client, limit: int = 100) -> int:
    delivered = 0
    with session_scope() as session:
        query = (select(OutboxMessage).where(OutboxMessage.status == "pending", OutboxMessage.next_attempt_at <= utcnow())
                 .order_by(OutboxMessage.created_at).limit(limit))
        if is_postgres(session):
            query = query.with_for_update(skip_locked=True)
        for message in session.scalars(query):
            tenant = session.get(Tenant, message.tenant_id)
            if tenant is None or not tenant.webhook_url:
                message.status = "skipped"
                continue
            body = callback_body(message)
            headers = {"Content-Type": "application/json", **sign(tenant.webhook_secret, body)}
            message.attempts += 1
            try:
                response = client.post(tenant.webhook_url, content=body, headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                message.last_error = str(exc)[:500]
                if message.attempts >= MAX_ATTEMPTS:
                    message.status = "dead"
                else:
                    message.next_attempt_at = utcnow() + timedelta(seconds=min(3600, 2 ** message.attempts * 5))
                continue
            message.status = "delivered"
            message.delivered_at = utcnow()
            delivered += 1
    return delivered


def run_once(judge: Judge, client: httpx.Client) -> tuple[int, int]:
    with session_scope() as session:
        evaluated = process_pending(session, judge)
    return evaluated, deliver_callbacks(client)


def run_forever(interval: float = 5.0) -> None:
    settings = get_settings()
    judge = build_judge(settings.judge_provider, settings.judge_model, settings.judge_api_key,
                        settings.judge_base_url, settings.judge_timeout_seconds)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with httpx.Client(timeout=settings.callback_timeout_seconds) as client:
        while not stopping:
            try:
                evaluated, delivered = run_once(judge, client)
                if evaluated or delivered:
                    log.info("evaluated %d tasks, delivered %d callbacks", evaluated, delivered)
            except Exception:
                log.exception("worker iteration failed")
            time.sleep(interval)
