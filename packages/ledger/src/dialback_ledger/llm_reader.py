"""The LLM document reader, `DIALBACK_READER=llm` (spec 0049).

A quarantined model with no tools sees one document's visible text and returns schema-checked JSON
that labels each phone number's role and party, with a quote. It never decides anything: code found
every number, grounding.py checks every quote, and the value always comes from the quote (R9, ADR-4).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import anthropic
import httpx2

MODEL = "claude-haiku-5-5"   # pinned here and nowhere else
TIMEOUT_S = 30.0             # per attempt; the SDK's default retries apply


def anthropic_client(api_key: str, http_client: httpx2.Client | None = None) -> anthropic.Anthropic:
    """The SDK client: the given key only (no other credential source), a 30-second timeout and the
    SDK's default retries."""
    return anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_S, http_client=http_client)


class LabelModel(Protocol):
    """The model seam. Given the text to label, return the model's parsed reply."""

    def label(self, text: str) -> object: ...


@dataclass(frozen=True)
class AnthropicLabelModel:
    """`LabelModel` over the Messages API, through the official SDK."""

    client: anthropic.Anthropic

    def label(self, text: str) -> object:
        raise NotImplementedError


@dataclass(frozen=True)
class LlmReader:
    """`Reader` over a `LabelModel`."""

    model: LabelModel

    def label(self, visible_text: str) -> list[dict]:
        raise NotImplementedError
