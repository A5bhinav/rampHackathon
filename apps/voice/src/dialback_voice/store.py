"""The database seam for POST /calls. The endpoint is pure of SQL: it asks the store for the
pre-dial facts, decides with `guards.pre_dial_refusal`, and (if the call is allowed) asks the
store to record it. Keeping this behind a Protocol lets the endpoint be tested with an in-memory
double and no Postgres; the real psycopg adapter is the thin piece the psql-backed CI test covers.

What the store reads lives in three places: the case in `app.cases` (exists, finished, held), the
call history in `voice.calls` (attempts for the case, AI calls to the number in 30 days, and the
Idempotency-Key for replay), and the chosen number in the Trust Ledger
(`ledger.number_selections` to `ledger.contact_points`, spec 0012 decision 8). The Postgres
adapter is `pg_store.py` (spec 0029). It fails closed on a missing pick: no provable number means
`has_number_pick=False`, which refuses the call with `number_not_selected` (R2, R7)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from .guards import PreDialInputs


@dataclass(frozen=True)
class PlacedCall:
    """A row in `voice.calls`: the call the core asked for, as it was accepted."""
    call_id: UUID
    case_id: UUID
    attempt: int


@dataclass(frozen=True)
class CallDial:
    """What the voice service knows about a placed call when Twilio calls back about it: enough to
    mint the call_token, speak the right disclosure and report the result, and no more. The case id
    never goes to Twilio or the model. Built from `voice.calls` plus the pick (spec 0013 Q47); the
    tests pass it directly, or go through `dial_from_row` below."""
    case_id: UUID
    attempt: int
    number_e164: str
    customer_name: str
    callback_number: str
    vendor_names: tuple[str, ...]
    # The Trust Ledger pick this call dials. Every call report carries it, so the core's decide()
    # can tell the call reached the pick (spec 0029). None only for a row that predates it.
    contact_point_id: UUID | None = None


def dial_from_row(row: Mapping[str, Any] | None) -> CallDial | None:
    """Assemble a `CallDial` from a joined call row, or `None` if the row is missing or any field
    the token and the disclosure need is absent. Fail closed (R2, R7): never invent a number or a
    name, so a route reads `None` and declines a call it only half knows. `vendor_names` is allowed
    to be empty (there may be no vendor to name; the disclosure does not use it). The query that
    builds the row (`pg_store.make_lookup`, spec 0029) joins `voice.calls`, `app.cases`, the vendor
    and the ledger pick; this mapper is the fixed contract it fills."""
    if row is None:
        return None
    required = ("case_id", "attempt", "number_e164", "customer_name", "callback_number")
    if any(row.get(key) in (None, "") for key in required):
        return None
    return CallDial(case_id=row["case_id"], attempt=int(row["attempt"]),
                    number_e164=row["number_e164"], customer_name=row["customer_name"],
                    callback_number=row["callback_number"],
                    vendor_names=tuple(row.get("vendor_names") or ()),
                    contact_point_id=row.get("contact_point_id"))


@dataclass(frozen=True)
class PreDial:
    """Everything POST /calls needs before dialing: the facts the decision reads, and the chosen
    number to record on the call (so a later dial can bind the one-time token to it)."""
    inputs: PreDialInputs
    contact_point_id: UUID | None
    number_e164: str | None


class CallStore(Protocol):
    async def find_call(self, idempotency_key: str) -> PlacedCall | None:
        """The call already recorded under this Idempotency-Key, or None. A repeat of an earlier
        request returns the first call rather than placing a second (internal.yaml)."""
        ...

    async def gather_pre_dial(self, *, case_id: UUID, attempt: int) -> PreDial:
        """The pre-dial facts for this case: the case's state, the call history, and the pick."""
        ...

    async def record_call(self, *, case_id: UUID, attempt: int, idempotency_key: str,
                          contact_point_id: UUID | None, number_e164: str | None,
                          agent_version: str) -> tuple[PlacedCall, bool]:
        """Insert the call and return it with True, or, if a racing request inserted the same
        Idempotency-Key first, return that existing call with False. The unique key is the guard,
        so two identical requests can never place two calls."""
        ...

    async def mark_dialed(self, call_id: UUID, *, provider_call_id: str) -> None:
        """Record that Twilio accepted the call: set provider='twilio', status='ringing', and the
        returned CallSid on the row (spec 0014). The call now runs over the WebSocket."""
        ...

    async def mark_failed(self, call_id: UUID, reason: str) -> None:
        """Record that the dial never placed a call: result='failed', with a short reason for the
        call log (never bank details). The core is told separately through the report (R7)."""
        ...
