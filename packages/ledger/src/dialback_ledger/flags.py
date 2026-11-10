"""The ledger's flags (and the LLM reader's key), read in this one module. Any unknown value falls
back to the safe path."""
from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum


class ReaderMode(StrEnum):
    STUB = "stub"   # safe: the keyword labeler, no model
    LLM = "llm"


def reader_mode(env: Mapping[str, str] | None = None) -> ReaderMode:
    value = (os.environ if env is None else env).get("DIALBACK_READER", "")
    return ReaderMode.LLM if value.strip().lower() == "llm" else ReaderMode.STUB


def anthropic_api_key(env: Mapping[str, str] | None = None) -> str | None:
    """The LLM reader's key (spec 0049), or None when it's unset or blank."""
    value = (os.environ if env is None else env).get("ANTHROPIC_API_KEY", "").strip()
    return value or None
