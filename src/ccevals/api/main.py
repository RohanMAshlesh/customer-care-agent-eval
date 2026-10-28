"""ASGI entry point: ``uvicorn ccevals.api.main:app``."""

from ccevals.api.app import create_app

app = create_app()
