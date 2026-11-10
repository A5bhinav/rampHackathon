"""The labeler: sees one document's visible text, says each value's role and party, and quotes it.

Its output is untrusted (R9) and goes through grounding.py before anything uses it. The `stub`
reader is a keyword labeler with no model (Q47). The `llm` reader is a quarantined model behind the
same port (llm_reader.py, spec 0049). It fails closed: a document it can't label raises
`ReaderFailed` and is never stored, and nothing ever falls back to the stub.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

import httpx2

from .candidates import parse_phone
from .flags import ReaderMode, anthropic_api_key


class Reader(Protocol):
    def label(self, visible_text: str) -> list: ...


class ReaderUnavailable(Exception):
    """The configured reader can't start (llm mode without its key). Nothing is labeled or stored."""


class ReaderFailed(Exception):
    """One document couldn't be labeled: an API error, a timeout, a refusal, a `max_tokens` stop, or a
    reply off the schema (`reason` says which). Fail closed: the document isn't stored, and no other
    reader is tried. The message holds the reason, the document and the API's request id; never
    document text or model output."""

    def __init__(self, reason: str, *, detail: str | None = None, request_id: str | None = None,
                 document_id: str | None = None):
        self.reason, self.detail, self.request_id, self.document_id = reason, detail, request_id, document_id
        what = reason + (f", {detail}" if detail else "") + (f", request {request_id}" if request_id else "")
        super().__init__((f"document {document_id}: " if document_id else "") + f"the reader failed ({what})")

    def for_document(self, document_id: str) -> ReaderFailed:
        return ReaderFailed(self.reason, detail=self.detail, request_id=self.request_id, document_id=document_id)


def _role(label_words: list[str]) -> str:
    if "fax" in label_words:
        return "fax"
    if "billing" in label_words:
        return "billing"
    if {"receivable", "ar", "remit"} & set(label_words):
        return "ar"
    if {"main", "phone", "office"} & set(label_words):
        return "main"
    return "unknown"


class StubReader:
    """One label per visible line that holds exactly one phone; the words before the colon say the role.

    The party is the vendor only when that label names one of the vendor's own lines (main, AR,
    billing, fax); a bank's number is the bank's; anything else is unknown, which the selection
    rule refuses (R7). So text the stub can't place never counts for a number.
    """

    def label(self, visible_text: str) -> list[dict]:
        out = []
        for line in visible_text.splitlines():
            if len(parse_phone(line)) != 1:
                continue
            label_words = re.findall(r"[a-z]+", line.split(":", 1)[0].lower()) if ":" in line else []
            role = _role(label_words)
            if "bank" in re.findall(r"[a-z]+", line.lower()):
                party = "bank"
            else:
                party = "vendor" if role != "unknown" else "unknown"
            out.append({"quote": line, "role": role, "party": party})
        return out


def reader_for(mode: ReaderMode, *, env: Mapping[str, str] | None = None,
               http_client: httpx2.Client | None = None) -> Reader:
    """The reader for `mode`. `llm` needs ANTHROPIC_API_KEY (from `env`, else the process's
    environment) and refuses to start without it. `http_client` is the SDK's HTTP client (tests pass
    one with a fake transport); by default the SDK builds its own."""
    if mode is ReaderMode.STUB:
        return StubReader()
    key = anthropic_api_key(env)
    if key is None:
        raise ReaderUnavailable("DIALBACK_READER=llm needs ANTHROPIC_API_KEY, which is unset: refusing to start")
    from .llm_reader import AnthropicLabelModel, LlmReader, anthropic_client
    return LlmReader(AnthropicLabelModel(anthropic_client(key, http_client)))
