"""Every feature flag the core reads, in one module (AGENTS.md, "Flags").

An unknown or missing value falls back to the safe path and is logged, never guessed at.
"""
from __future__ import annotations

import logging
import os
from enum import StrEnum

log = logging.getLogger(__name__)


class RampMode(StrEnum):
    MOCK = "mock"        # safe: the Ramp-shaped mock, no real business
    SANDBOX = "sandbox"


class ResolverMode(StrEnum):
    STUB = "stub"        # safe: the ledger package's stub (Inesh)
    LEDGER = "ledger"


class EmailMode(StrEnum):
    OFF = "off"          # safe: no email door
    RESEND = "resend"


def read_flag[E: StrEnum](name: str, kind: type[E], safe: E, environ: dict[str, str] | None = None) -> E:
    raw = (environ if environ is not None else os.environ).get(name)
    if raw is None:
        return safe
    try:
        return kind(raw.strip().lower())
    except ValueError:
        log.warning("unknown value %r for %s; using the safe default %r", raw, name, safe.value)
        return safe
