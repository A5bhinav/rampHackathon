"""Configuration, read once at startup. A missing secret stops the service (R7: fail closed)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .email_door.signature import MIN_KEY_BYTES, decode_secret
from .flags import EmailMode, RampMode, ResolverMode, read_flag


class ConfigurationError(RuntimeError):
    pass


def _secret(environ: dict[str, str], name: str, min_len: int = 16) -> bytes:
    value = environ.get(name, "")
    if len(value) < min_len:
        raise ConfigurationError(f"{name} is required (at least {min_len} characters)")
    return value.encode()


# Secrets committed to this repo for the local stack and tests. A core built from the
# environment (a deployment) refuses them, so nobody can mint a token with a value anyone can read.
COMMITTED_DEV_SECRETS = frozenset({"dev-jwt-secret-0123456789abcdef0123456789", "jwt-secret-for-tests-only-0123456789abcdef"})


def _supabase_url(environ: dict[str, str]) -> str | None:
    """The project's origin, exactly: https, a host, no user, path, query or fragment. A local
    Supabase (or the stand-in) is only reachable through scripts/dev/stack.py, never from the env."""
    raw = environ.get("SUPABASE_URL", "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ConfigurationError("SUPABASE_URL must be the project's address alone, like https://<ref>.supabase.co")
    return f"https://{parts.netloc}"


def _jwt_secret(environ: dict[str, str], supabase_url: str | None) -> bytes:
    raw = environ.get("DIALBACK_JWT_SECRET", "")
    if not raw:
        if supabase_url is None:
            raise ConfigurationError("Set SUPABASE_URL (Supabase sign-in) or DIALBACK_JWT_SECRET (local dev sign-in)")
        return b""
    if raw in COMMITTED_DEV_SECRETS:
        raise ConfigurationError("DIALBACK_JWT_SECRET is a secret committed to the repo for local use; a deployed core never trusts it")
    if supabase_url is not None and environ.get("DIALBACK_TRUST_LEGACY_SECRET") != "1":
        raise ConfigurationError("DIALBACK_JWT_SECRET is set alongside SUPABASE_URL. Supabase advises against trusting the shared "
                                 "secret: unset it, or set DIALBACK_TRUST_LEGACY_SECRET=1 only while the project moves off it")
    return _secret(environ, "DIALBACK_JWT_SECRET", 32)


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
    # Spoken digits are hashed with this key before anything is stored (spec 0002, D7).
    digits_key: bytes = b""
    digits_key_version: int = 1
    # Service-to-service token for the voice service's call reports (internal.yaml).
    internal_api_token: bytes = b""
    voice_base_url: str | None = None
    # When calls may be placed, in the vendor's time zone (design: Mon-Fri 9:00-16:30).
    calling_tz: str = "America/Los_Angeles"
    calling_window: str = "09:00-16:30"
    calling_days: str = "mon-fri"
    # Demo datasets only (spec 0011, D8): every demo number is a team phone, and the final is in the evening.
    demo_calling_window: str = "always"
    # Where the Ramp mock sends a demo reset's webhooks (spec 0011, D2). Unset: the poller finds changes.
    core_public_url: str | None = None
    # Worker pacing.
    command_backoff_seconds: float = 5.0
    lease_seconds: int = 300
    worker_poll_seconds: float = 1.0
    run_worker: bool = True
    # Scheduled jobs (spec 0003), due on each dataset's clock.
    run_jobs: bool = True
    poll_interval_seconds: int = 60
    full_sweep_interval_seconds: int = 86_400
    reconcile_interval_seconds: int = 300
    verify_interval_seconds: int = 300
    job_lease_seconds: int = 600
    # The dashboard's JWTs (spec 0010). At least one key source: the Supabase project, whose
    # public signing keys and issuer come from its URL, and/or a shared HS256 secret (local dev,
    # or a project still on Supabase's legacy secret).
    supabase_url: str | None = None
    jwt_secret: bytes = b""
    jwt_audience: str = "authenticated"
    # The email door (spec 0085). With `resend`, the key and the webhook signing secret are required.
    email_mode: EmailMode = EmailMode.OFF
    resend_api_key: str | None = None
    resend_webhook_secret: str | None = None
    inbox_address: str | None = None
    reply_from: str | None = None             # unset: replies are not configured
    bank_changes_url: str | None = None
    company_name: str = "the company you're writing to"
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if environ is None else environ)
        url = env.get("DATABASE_URL")
        if not url:
            raise ConfigurationError("DATABASE_URL is required")
        ramp_mode = read_flag("DIALBACK_RAMP", RampMode, RampMode.MOCK, env)
        supabase_url = _supabase_url(env)
        email_mode = read_flag("DIALBACK_EMAIL", EmailMode, EmailMode.OFF, env)
        if email_mode is EmailMode.RESEND:
            for name in ("RESEND_API_KEY", "RESEND_WEBHOOK_SECRET"):
                if not env.get(name, "").strip():
                    raise ConfigurationError(f"{name} is required when DIALBACK_EMAIL=resend")
            try:
                key = decode_secret(env["RESEND_WEBHOOK_SECRET"].strip())
            except ValueError:
                key = b""
            if len(key) < MIN_KEY_BYTES:
                raise ConfigurationError(
                    f"RESEND_WEBHOOK_SECRET must be Resend's signing secret (whsec_ and base64 of at least {MIN_KEY_BYTES} bytes)")
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
            digits_key=_secret(env, "DIGITS_KEY", 32),
            digits_key_version=int(env.get("DIGITS_KEY_VERSION", "1")),
            internal_api_token=_secret(env, "INTERNAL_API_TOKEN", 24),
            voice_base_url=(env.get("VOICE_URL") or "").rstrip("/") or None,
            calling_tz=env.get("DIALBACK_CALLING_TZ", "America/Los_Angeles"),
            calling_window=env.get("DIALBACK_CALLING_WINDOW", "09:00-16:30"),
            calling_days=env.get("DIALBACK_CALLING_DAYS", "mon-fri"),
            demo_calling_window=env.get("DIALBACK_DEMO_CALLING_WINDOW", "always"),
            core_public_url=(env.get("CORE_PUBLIC_URL") or "").rstrip("/") or None,
            run_worker=env.get("DIALBACK_RUN_WORKER", "1") != "0",
            run_jobs=env.get("DIALBACK_RUN_JOBS", "1") != "0",
            poll_interval_seconds=int(env.get("DIALBACK_POLL_SECONDS", "60")),
            full_sweep_interval_seconds=int(env.get("DIALBACK_FULL_SWEEP_SECONDS", "86400")),
            reconcile_interval_seconds=int(env.get("DIALBACK_RECONCILE_SECONDS", "300")),
            verify_interval_seconds=int(env.get("DIALBACK_VERIFY_SECONDS", "300")),
            email_mode=email_mode,
            resend_api_key=(env.get("RESEND_API_KEY") or "").strip() or None,
            resend_webhook_secret=(env.get("RESEND_WEBHOOK_SECRET") or "").strip() or None,
            inbox_address=(env.get("DIALBACK_INBOX_ADDRESS") or "").strip() or None,
            reply_from=(env.get("DIALBACK_REPLY_FROM") or "").strip() or None,
            bank_changes_url=(env.get("DIALBACK_BANK_CHANGES_URL") or "").strip() or None,
            company_name=(env.get("DIALBACK_COMPANY_NAME") or "").strip() or "the company you're writing to",
            supabase_url=supabase_url,
            jwt_secret=_jwt_secret(env, supabase_url),
        )
