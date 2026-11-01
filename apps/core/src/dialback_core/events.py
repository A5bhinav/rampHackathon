"""The only way the core moves a case: app.apply_case_event() (ADR-1).

This wrapper names the actor, maps the database's refusals to typed errors, and returns
the function's result. It adds no rules of its own; the transition table and its guards
decide.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection, errors
from psycopg.types.json import Jsonb

SYSTEM: dict[str, Any] = {"kind": "system", "id": None}


def person(user_id: UUID) -> dict[str, Any]:
    return {"kind": "person", "id": str(user_id)}


class CaseEventError(Exception):
    """The database refused the event."""


class TransitionRefused(CaseEventError):
    """No such move from the case's state, or its guard failed (SQLSTATE 23514)."""


class NotAllowed(CaseEventError):
    """This actor may not make this move (SQLSTATE 42501)."""


class VersionConflict(CaseEventError):
    """The case changed since the person loaded it (SQLSTATE 40001)."""


@dataclass(frozen=True)
class EventResult:
    status: str                  # applied | already_applied | awaiting_second_approver
    case_id: UUID
    from_state: str | None
    to_state: str
    version: int
    evidence_seq: int | None
    raw: dict[str, Any]

    @property
    def applied(self) -> bool:
        return self.status != "already_applied"

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> EventResult:
        return cls(status=raw["status"], case_id=UUID(raw["case_id"]), from_state=raw.get("from_state"),
                   to_state=raw["to_state"], version=int(raw["version"]), evidence_seq=raw.get("evidence_seq"), raw=raw)


def translate(exc: errors.Error) -> CaseEventError | None:
    message = exc.diag.message_primary or str(exc)
    if isinstance(exc, errors.CheckViolation):
        return TransitionRefused(message)
    if isinstance(exc, errors.InsufficientPrivilege):
        return NotAllowed(message)
    if isinstance(exc, errors.SerializationFailure):
        return VersionConflict(message)
    return None


async def apply_event(conn: AsyncConnection, case_id: UUID | None, event_type: str, idempotency_key: str,
                      body: dict[str, Any] | None = None, actor: dict[str, Any] = SYSTEM) -> EventResult:
    """Apply one case event inside the caller's transaction.

    A refusal is raised as a typed error after rolling back to a savepoint, so the caller's
    transaction stays usable (for example to record why a command stopped).
    """
    try:
        async with conn.transaction():  # a savepoint inside the caller's transaction
            row = await (await conn.execute(
                "select app.apply_case_event(%s, %s, %s, %s, %s) as result",
                (case_id, event_type, Jsonb(actor), idempotency_key, Jsonb(body or {})))).fetchone()
    except errors.Error as exc:
        mapped = translate(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return EventResult.parse(row["result"])
