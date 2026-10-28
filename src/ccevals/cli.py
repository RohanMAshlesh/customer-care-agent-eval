"""Command-line entry point: ``ccevals serve | worker | db upgrade | tenant create | key issue``."""

from __future__ import annotations

import argparse
import logging
import secrets
import sys
from pathlib import Path


def _alembic_config():
    from alembic.config import Config

    root = Path.cwd() if (Path.cwd() / "alembic.ini").exists() else Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ccevals")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--workers", type=int, default=2)

    worker = sub.add_parser("worker", help="evaluate, finalise deadlines and deliver callbacks")
    worker.add_argument("--interval", type=float, default=5.0)
    worker.add_argument("--once", action="store_true")

    db = sub.add_parser("db", help="database migrations")
    db.add_argument("action", choices=["upgrade", "create-all"])

    tenant = sub.add_parser("tenant", help="tenant administration")
    tenant_sub = tenant.add_subparsers(dest="tenant_command", required=True)
    create = tenant_sub.add_parser("create")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--region", default=None)
    create.add_argument("--webhook-url", default=None)

    key = sub.add_parser("key", help="API keys")
    key_sub = key.add_subparsers(dest="key_command", required=True)
    issue = key_sub.add_parser("issue")
    issue.add_argument("--tenant", required=True)
    issue.add_argument("--name", required=True)
    issue.add_argument("--roles", required=True, help="comma separated")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.command == "serve":
        import uvicorn

        uvicorn.run("ccevals.api.main:app", host=args.host, port=args.port, workers=args.workers, proxy_headers=True)
        return 0
    if args.command == "worker":
        from ccevals import worker as worker_mod

        if args.once:
            import httpx

            from ccevals.config import get_settings
            from ccevals.evaluation.semantic import build_judge

            s = get_settings()
            judge = build_judge(s.judge_provider, s.judge_model, s.judge_api_key, s.judge_base_url, s.judge_timeout_seconds)
            with httpx.Client(timeout=s.callback_timeout_seconds) as client:
                evaluated, delivered = worker_mod.run_once(judge, client)
            print(f"evaluated={evaluated} delivered={delivered}")
        else:
            worker_mod.run_forever(args.interval)
        return 0
    if args.command == "db":
        if args.action == "upgrade":
            from alembic import command

            command.upgrade(_alembic_config(), "head")
        else:
            from ccevals.db import create_all

            create_all()
        return 0

    from ccevals.config import get_settings
    from ccevals.db import session_scope
    from ccevals.models import Tenant
    from ccevals.security import issue_key

    if args.command == "tenant":
        with session_scope() as session:
            if session.get(Tenant, args.id):
                print(f"tenant {args.id} already exists", file=sys.stderr)
                return 1
            session.add(Tenant(id=args.id, name=args.name, region=args.region or get_settings().region,
                               webhook_url=args.webhook_url, webhook_secret=secrets.token_urlsafe(32)))
            session.flush()
            token = issue_key(session, args.id, "bootstrap-admin", ["admin"])
        print(f"tenant {args.id} created")
        print(f"admin API key (shown once): {token}")
        return 0
    if args.command == "key":
        with session_scope() as session:
            token = issue_key(session, args.tenant, args.name, [r.strip() for r in args.roles.split(",") if r.strip()])
        print(token)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
