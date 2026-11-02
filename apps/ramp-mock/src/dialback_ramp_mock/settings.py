"""Configuration, read once from the environment. Missing secrets fail at startup."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    token_secret: bytes
    deliver_webhooks: bool = True
    webhook_poll_seconds: float = 0.5
    webhook_timeout_seconds: float = 5.0
    webhook_max_attempts: int = 5

    @classmethod
    def from_env(cls) -> Settings:
        url = os.environ.get("DATABASE_URL")
        secret = os.environ.get("MOCK_RAMP_SECRET")
        if not url:
            raise RuntimeError("DATABASE_URL is required")
        if not secret or len(secret) < 16:
            raise RuntimeError("MOCK_RAMP_SECRET is required (at least 16 characters); it signs per-dataset tokens")
        return cls(
            database_url=url,
            token_secret=secret.encode(),
            deliver_webhooks=os.environ.get("MOCK_RAMP_DELIVER_WEBHOOKS", "1") != "0",
        )
