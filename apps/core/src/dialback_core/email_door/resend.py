"""The Resend port and its HTTP client (spec 0085).

`get_received` fetches one received email. Its body (`text`, `html`, `raw`) lives in memory only: the
client keeps the sha256 of `raw` and drops the rest of what it does not need. Nothing here logs an
email's content; errors name the status code, never a response body.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import quote

import httpx

API = "https://api.resend.com"
BLOCK_TAGS = frozenset({"p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6",
                        "blockquote", "pre", "hr", "section", "article", "header", "footer"})
SKIPPED_TAGS = frozenset({"script", "style", "title"})


@dataclass(frozen=True)
class ReceivedEmail:
    email_id: str
    message_id: str
    sender: str                       # the From header
    reply_to: tuple[str, ...]
    to: tuple[str, ...]
    subject: str
    text: str = field(repr=False)     # Resend's text, else the html turned into text
    html_text: str = field(repr=False)  # the html part turned into text, "" if none (mail clients show this part)
    headers: dict[str, str]           # lowercase keys
    authentication: dict[str, str] | None   # spf, dkim, dmarc
    raw_sha256: str
    has_attachments: bool


class ResendError(Exception):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ResendPort(Protocol):
    async def get_received(self, email_id: str) -> ReceivedEmail: ...

    async def send(self, *, sender: str, to: str, subject: str, text: str, in_reply_to: str | None) -> str: ...


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in SKIPPED_TAGS:
            self.skipping += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS:
            self.skipping = max(0, self.skipping - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Plain text from html: block tags become newlines, tags and scripts vanish, runs of spaces collapse."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def _digest(raw: Any) -> str:
    data = raw.encode() if isinstance(raw, str) else json.dumps(raw, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def _fail(status: int) -> ResendError:
    return ResendError(f"resend answered {status}", retryable=status == 429 or status >= 500)


class ResendClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient) -> None:
        self._key, self._http = api_key, http

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            r = await self._http.request(method, API + path, headers={"Authorization": f"Bearer {self._key}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise ResendError(f"resend unreachable: {type(exc).__name__}", retryable=True) from None
        if r.status_code >= 300:
            raise _fail(r.status_code)
        try:
            body = r.json()
        except ValueError:
            raise ResendError("resend answered with unreadable json") from None
        if not isinstance(body, dict):
            raise ResendError("resend answered with an unexpected shape")
        return body

    async def get_received(self, email_id: str) -> ReceivedEmail:
        body = await self._call("GET", f"/emails/receiving/{quote(email_id, safe='')}")
        headers = {str(k).lower(): v if isinstance(v, str) else ", ".join(_strings(v))
                   for k, v in (body.get("headers") or {}).items()}
        auth = body.get("authentication")
        html_text = html_to_text(body.get("html") or "")
        text = body.get("text") or html_text
        return ReceivedEmail(
            email_id=email_id, message_id=str(body.get("message_id") or headers.get("message-id") or ""),
            sender=str(body.get("from") or ""), reply_to=_strings(body.get("reply_to")), to=_strings(body.get("to")),
            subject=str(body.get("subject") or ""), text=text, html_text=html_text, headers=headers,
            authentication={k: str(auth.get(k) or "unknown") for k in ("spf", "dkim", "dmarc")} if isinstance(auth, dict) else None,
            raw_sha256=_digest(body.get("raw") or ""), has_attachments=bool(body.get("attachments")))

    async def send(self, *, sender: str, to: str, subject: str, text: str, in_reply_to: str | None) -> str:
        payload: dict[str, Any] = {"from": sender, "to": [to], "subject": subject, "text": text}
        if in_reply_to:
            payload["headers"] = {"In-Reply-To": in_reply_to, "References": in_reply_to}
        body = await self._call("POST", "/emails", json=payload)
        return str(body.get("id") or "")
