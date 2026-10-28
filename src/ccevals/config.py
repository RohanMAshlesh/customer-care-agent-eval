"""Runtime settings, read from environment variables prefixed with CCEVALS_."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(f"CCEVALS_{name}", default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./ccevals.db"))
    region: str = field(default_factory=lambda: _env("REGION", "demo-region-1"))
    environment: str = field(default_factory=lambda: _env("ENVIRONMENT", "development"))
    # Secret used to derive tenant-scoped identifier tokens. Must be overridden in production.
    tokenization_secret: str = field(default_factory=lambda: _env("TOKENIZATION_SECRET", "dev-only-secret"))
    max_batch_events: int = field(default_factory=lambda: int(_env("MAX_BATCH_EVENTS", "500")))
    max_request_bytes: int = field(default_factory=lambda: int(_env("MAX_REQUEST_BYTES", str(5 * 1024 * 1024))))
    rate_limit_per_minute: int = field(default_factory=lambda: int(_env("RATE_LIMIT_PER_MINUTE", "600")))
    # Evaluate dirty tasks inline after ingestion (useful for small deployments and tests).
    inline_evaluation: bool = field(default_factory=lambda: _env("INLINE_EVALUATION", "true").lower() == "true")
    decision_validity_days: int = field(default_factory=lambda: int(_env("DECISION_VALIDITY_DAYS", "7")))
    # Semantic judge. "none" means semantic criteria are reported as not evaluated.
    judge_provider: str = field(default_factory=lambda: _env("JUDGE_PROVIDER", "none"))
    judge_model: str = field(default_factory=lambda: _env("JUDGE_MODEL", "claude-opus-5-5"))
    judge_api_key: str = field(default_factory=lambda: _env("JUDGE_API_KEY", ""))
    judge_base_url: str = field(default_factory=lambda: _env("JUDGE_BASE_URL", "https://api.anthropic.com"))
    judge_timeout_seconds: float = field(default_factory=lambda: float(_env("JUDGE_TIMEOUT_SECONDS", "30")))
    callback_timeout_seconds: float = field(default_factory=lambda: float(_env("CALLBACK_TIMEOUT_SECONDS", "10")))

    def validate_for_production(self) -> list[str]:
        problems = []
        if self.environment == "production":
            if self.tokenization_secret == "dev-only-secret":
                problems.append("CCEVALS_TOKENIZATION_SECRET must be set in production")
            if self.database_url.startswith("sqlite"):
                problems.append("CCEVALS_DATABASE_URL must point at PostgreSQL in production")
            if self.judge_provider != "none" and not self.judge_api_key:
                problems.append("CCEVALS_JUDGE_API_KEY is required when a judge provider is configured")
        return problems


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
