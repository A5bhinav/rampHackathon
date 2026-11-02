"""The two places the core reaches another slice (spec 0002, D10 and D11).

- NumberResolver: the Trust Ledger's `select_number` (Inesh). `DIALBACK_RESOLVER=stub`
  calls the ledger package's stub, `ledger` its real rule. If the ledger doesn't provide the
  function yet, the resolver returns "no provable number": the case goes to a person (R7).
- CallPlacer: the voice service's `POST /calls` (Sami, openapi/internal.yaml).

Tests inject doubles for both; production code never contains a stand-in for another
slice's work.
"""
from __future__ import annotations

import datetime as dt
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx

from .flags import ResolverMode

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Selection:
    """What the core needs from a selection (D11). A pick has a contact point."""
    number_selection_id: UUID | None
    rule_version: str
    contact_point_id: UUID | None = None
    reason: str | None = None
    source_types: tuple[str, ...] = ()

    @property
    def picked(self) -> bool:
        return self.contact_point_id is not None and self.number_selection_id is not None


class NumberResolver(Protocol):
    async def select_number(self, *, case_id: UUID, dataset_id: UUID, vendor_id: UUID, cutoff: dt.datetime) -> Selection: ...


def selection_from(raw: dict[str, Any]) -> Selection:
    def uuid_or_none(v: Any) -> UUID | None:
        return UUID(str(v)) if v else None
    return Selection(
        number_selection_id=uuid_or_none(raw.get("number_selection_id")),
        rule_version=str(raw.get("rule_version") or "unknown"),
        contact_point_id=uuid_or_none(raw.get("contact_point_id")),
        reason=raw.get("reason"),
        source_types=tuple(raw.get("source_types") or ()),
    )


class LedgerResolver:
    """Calls `dialback_ledger.select_number_stub` or `dialback_ledger.select_number`."""

    def __init__(self, mode: ResolverMode) -> None:
        self.function_name = "select_number_stub" if mode is ResolverMode.STUB else "select_number"

    async def select_number(self, *, case_id: UUID, dataset_id: UUID, vendor_id: UUID, cutoff: dt.datetime) -> Selection:
        try:
            import dialback_ledger
            fn = getattr(dialback_ledger, self.function_name)
        except (ImportError, AttributeError):
            return Selection(None, "unavailable", reason=f"The Trust Ledger's {self.function_name} is not available yet, "
                                                         "so no number is provable.")
        raw = fn(case_id=case_id, dataset_id=dataset_id, vendor_id=vendor_id, cutoff=cutoff)
        if inspect.isawaitable(raw):
            raw = await raw
        return selection_from(raw)


class PreDialRefused(Exception):
    """The voice service checked the call and placed none (409 PreDialRefusal)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class CallerError(Exception):
    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class CallPlacer(Protocol):
    async def place_call(self, *, case_id: UUID, attempt: int, idempotency_key: str) -> UUID: ...


class VoiceServiceClient:
    """POST {voice}/calls with {case_id, attempt}; the voice service reads the number itself."""

    def __init__(self, base_url: str, token: bytes, http: httpx.AsyncClient) -> None:
        self.base_url, self._token, self._http = base_url.rstrip("/"), token.decode(), http

    async def place_call(self, *, case_id: UUID, attempt: int, idempotency_key: str) -> UUID:
        try:
            r = await self._http.post(f"{self.base_url}/calls", json={"case_id": str(case_id), "attempt": attempt},
                                      headers={"Authorization": f"Bearer {self._token}", "Idempotency-Key": idempotency_key})
        except httpx.TransportError as exc:
            raise CallerError(f"voice service unreachable: {type(exc).__name__}", retryable=True) from exc
        if r.status_code in (200, 202):
            return UUID(r.json()["call_id"])
        if r.status_code == 409:
            body = r.json()
            raise PreDialRefused(body.get("code", "unknown"), body.get("message", ""))
        raise CallerError(f"voice service answered HTTP {r.status_code}", retryable=r.status_code >= 500)


class NoVoiceService:
    """Wired when VOICE_URL is unset: every call is refused, so the case goes to a person (R7)."""

    async def place_call(self, *, case_id: UUID, attempt: int, idempotency_key: str) -> UUID:
        raise PreDialRefused("voice_not_configured", "VOICE_URL is not set, so no call can be placed.")
