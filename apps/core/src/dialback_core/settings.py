"""Configuration, read once at startup. A missing secret stops the service (R7: fail closed)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .flags import RampMode, ResolverMode, read_flag


class ConfigurationError(RuntimeError):
    pass


def _secret(environ: dict[str, str], name: str, min_len: int = 16) -> bytes:
    value = environ.get(name, "")
    if len(value) < min_len:
        raise ConfigurationError(f"{name} is required (at least {min_len} characters)")
    return value.encode()


@dataclass(frozen=True)
class Settings:
    database_url: str
    ramp_mode: RampMode
    ramp_base_url: str
    ramp_webhook_secret: bytes
    fingerprint_key: bytes
    fingerprint_key_version: int = 1
    mock_ramp_secret: bytes | None = None
    resolver_mode: ResolverMode = ResolverMode.STUB
    ramp_requests_per_10s: int = 150          # stay under Ramp's 200 per 10 s with headroom
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if environ is None else environ)
        url = env.get("DATABASE_URL")
        if not url:
            raise ConfigurationError("DATABASE_URL is required")
        ramp_mode = read_flag("DIALBACK_RAMP", RampMode, RampMode.MOCK, env)
        base = env.get("RAMP_BASE_URL")
        if not base:
            raise ConfigurationError("RAMP_BASE_URL is required (the mock's URL, or Ramp's sandbox API)")
        return cls(
            database_url=url,
            ramp_mode=ramp_mode,
            ramp_base_url=base.rstrip("/"),
            ramp_webhook_secret=_secret(env, "RAMP_WEBHOOK_SECRET"),
            fingerprint_key=_secret(env, "FINGERPRINT_KEY", 32),
            fingerprint_key_version=int(env.get("FINGERPRINT_KEY_VERSION", "1")),
            mock_ramp_secret=_secret(env, "MOCK_RAMP_SECRET") if ramp_mode is RampMode.MOCK else None,
            resolver_mode=read_flag("DIALBACK_RESOLVER", ResolverMode, ResolverMode.STUB, env),
        )
