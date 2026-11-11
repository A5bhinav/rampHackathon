"""The core worker: runs every queued command exactly once and fires due timers (ADR-3).

One tick: process stored Ramp webhooks, run due scheduled jobs (spec 0003), fire due timers, run
due commands, then process stored emails (spec 0085, last and time-bounded). An error in either
inbound step ends that step only. Each command is
claimed in its own short transaction (FOR UPDATE SKIP LOCKED, with a lease so a crashed
worker's command is picked up again), run with Ramp and voice calls outside any
transaction, then finished in a second transaction:

- success: done.
- Defer(t): run again at t without counting an attempt (outside the calling window).
- Stale: the case moved on (a newer change, a different state); dropped, nothing escalated.
- any other failure: retried with backoff; after max_attempts the case goes to a person with
  the hold still on (hold_failed from `open`, command_failed elsewhere). R7.

Every time the worker compares or writes comes from the dataset's clock (spec 0002, D2).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from dialback_decision import CallChecks, decide
from dialback_decision.facts import earlier_call, facts_from_answers, values
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .calling_window import CallingWindow
from .company import every_name_matches
from .db import dataset_now, system_tx
from .detection import Keys, process_inbound_webhooks
from .email_door.classifier import ChangeClassifier
from .email_door.inbox import process_inbound_emails
from .email_door.resend import ResendPort
from .events import TransitionRefused, apply_event
from .jobs import Jobs, unique_worker_name
from .ports import CallerError, CallPlacer, ChangeContext, LedgerChangeContext, NumberResolver, PreDialRefused
from .ramp import RampConnector, RampError, RampNotFound, RampPort
from .settings import Settings
from .timeline import PRE_DIAL

log = logging.getLogger(__name__)
HOLD_REASON = "Dialback: the vendor's bank details changed; verifying with the vendor before paying."


class Stale(Exception):
    """The case has moved on; this command no longer applies."""


class Defer(Exception):
    def __init__(self, until: dt.datetime, why: str) -> None:
        super().__init__(why)
        self.until = until


class Retry(Exception):
    """A failure worth trying again (and escalating if it keeps failing)."""


@dataclass(frozen=True)
class Command:
    id: int
    dataset_id: UUID
    case_id: UUID | None
    command: str
    key: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


CLAIM_COMMAND = """
update app.outbox o
   set status = 'running', locked_at = clock_timestamp(), locked_by = %(worker)s, attempts = o.attempts + 1
 where o.id = (
   select o2.id from app.outbox o2 join app.datasets d on d.id = o2.dataset_id
    where ((o2.status = 'pending' and o2.run_after <= coalesce(case when d.kind = 'lab' then d.sim_now end, clock_timestamp()))
        or (o2.status = 'running' and o2.locked_at < clock_timestamp() - make_interval(secs => %(lease)s)))
      and (%(dataset)s::uuid is null or o2.dataset_id = %(dataset)s::uuid)
    order by o2.run_after, o2.id
    for update of o2 skip locked
    limit 1)
returning o.id, o.dataset_id, o.case_id, o.command, o.idempotency_key, o.payload, o.attempts, o.max_attempts
"""

CLAIM_TIMER = """
select t.* from app.timers t join app.datasets d on d.id = t.dataset_id
 where t.status = 'scheduled'
   and t.fire_at <= coalesce(case when d.kind = 'lab' then d.sim_now end, clock_timestamp())
   and (%(dataset)s::uuid is null or t.dataset_id = %(dataset)s::uuid)
 order by t.fire_at, t.id
 for update of t skip locked
 limit 1
"""

CASE = """
select c.*, v.ramp_vendor_id, v.legal_name, v.trade_names, ce.detected_at, ce.ramp_changed_at,
       n.account_last4 as new_last4, o.account_last4 as old_last4
  from app.cases c
  join app.vendors v on v.id = c.vendor_id
  join app.bank_change_events ce on ce.id = c.change_event_id
  join app.bank_accounts n on n.id = ce.new_account_id
  left join app.bank_accounts o on o.id = ce.old_account_id
 where c.id = %s
"""

# The deadline acts only while code owns the case; otherwise it re-checks (spec 0007, D4).
DEADLINE_ACTIVE = ("held", "calling", "retry_wait", "deciding")
DEADLINE_RECHECK = dt.timedelta(hours=1)

# This generation's evidence the decision reads (spec 0005): the ledger's pick and every call report.
GENERATION = """
select type, body_jcs::jsonb -> 'body' as body
  from audit.evidence_events
 where case_id = %s and (body_jcs::jsonb ->> 'version')::int = %s and type in ('number_resolved', 'call_ended')
 order by seq
"""


class Worker:
    def __init__(self, pool: AsyncConnectionPool, settings: Settings, connector: RampConnector, resolver: NumberResolver,
                 caller: CallPlacer, name: str | None = None, sleep=asyncio.sleep,
                 change_context: ChangeContext | None = None, resend: ResendPort | None = None,
                 classifier: ChangeClassifier | None = None) -> None:
        self.pool, self.settings, self.connector = pool, settings, connector
        self.resend = resend
        self.classifier = classifier   # the email door's model, with DIALBACK_EMAIL_CLASSIFIER=model only
        self.resolver, self.caller, self.name, self._sleep = resolver, caller, name or unique_worker_name(), sleep
        self.change_context = change_context or LedgerChangeContext()
        self.keys = Keys(settings.fingerprint_key, settings.fingerprint_key_version)
        self.window = CallingWindow.parse(settings.calling_tz, settings.calling_window, settings.calling_days)
        self.demo_window = CallingWindow.parse_spec(settings.calling_tz, settings.demo_calling_window)
        self.jobs = Jobs(pool, settings, connector, self.keys, self.name)
        self.handlers = {
            "place_hold": self.place_hold, "select_number": self.select_number, "place_call": self.place_call,
            "decide": self.decide, "release_hold": self.release_hold, "notify": self.notify,
        }

    # The loop ---------------------------------------------------------------------------------

    async def tick(self, dataset_id: UUID | None = None) -> int:
        done = await self._contained("Ramp webhook", process_inbound_webhooks(self.pool, self.connector, self.keys,
                                                                              dataset_id=dataset_id))
        if self.settings.run_jobs:
            done += await self.jobs.run_due(dataset_id)  # the poller and reconciliation (spec 0003)
        done += await self.fire_timers(dataset_id)
        done += await self.run_commands(dataset_id)
        # The email door last (spec 0085; final review finding I1): its fetches and model calls are slow and
        # can fail, so it runs after the holds, calls and releases, within its own time budget.
        if self.resend is not None:
            done += await self._contained("email", process_inbound_emails(self.pool, self.settings, self.resend, self.keys,
                                                                          dataset_id=dataset_id, classifier=self.classifier))
        return done

    @staticmethod
    async def _contained(step: str, work: Awaitable[int]) -> int:
        """Run one inbound step; an error ends that step only, never the tick, so the commands still run (R1).
        Only the error's type name is logged: its message can hold a webhook's or an email's values (ADR-8)."""
        try:
            return await work
        except Exception as exc:  # noqa: BLE001  contained; the step runs again next tick
            log.warning("worker: the %s step failed: %s", step, type(exc).__name__)
            return 0

    async def drain(self, dataset_id: UUID | None = None, max_ticks: int = 50) -> int:
        """Tick until nothing is due. For tests, the smoke run and the lab."""
        total = 0
        for _ in range(max_ticks):
            n = await self.tick(dataset_id)
            total += n
            if n == 0:
                return total
        raise RuntimeError("the worker kept finding work; is something looping?")

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                busy = await self.tick()
            except Exception:
                log.exception("worker tick failed")
                busy = 0
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.settings.worker_poll_seconds)
                except TimeoutError:
                    pass

    # Commands --------------------------------------------------------------------------------

    async def run_commands(self, dataset_id: UUID | None, limit: int = 20) -> int:
        ran = 0
        for _ in range(limit):
            async with system_tx(self.pool) as conn:
                row = await (await conn.execute(CLAIM_COMMAND, {"worker": self.name, "lease": self.settings.lease_seconds,
                                                                "dataset": dataset_id})).fetchone()
            if row is None:
                return ran
            cmd = Command(row["id"], row["dataset_id"], row["case_id"], row["command"], row["idempotency_key"],
                          row["payload"] or {}, row["attempts"], row["max_attempts"])
            await self._run(cmd)
            ran += 1
        return ran

    async def _run(self, cmd: Command) -> None:
        outcome, detail, until = "done", None, None
        try:
            await self.handlers[cmd.command](cmd)
        except Defer as d:
            outcome, detail, until = "defer", str(d), d.until
        except Stale as s:
            outcome, detail = "stale", str(s)
        except (Retry, RampError, CallerError) as exc:
            outcome, detail = "retry", f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            log.exception("command %s failed", cmd.key)
            outcome, detail = "retry", f"{type(exc).__name__}"
        await self._finish(cmd, outcome, detail, until)

    async def _finish(self, cmd: Command, outcome: str, detail: str | None, until: dt.datetime | None) -> None:
        async with system_tx(self.pool) as conn:
            now = await dataset_now(conn, cmd.dataset_id)
            if outcome == "done":
                await conn.execute("update app.outbox set status = 'done', done_at = %s, last_error = null, locked_at = null "
                                   "where id = %s", (now, cmd.id))
            elif outcome == "defer":
                await conn.execute("update app.outbox set status = 'pending', run_after = %s, attempts = attempts - 1, "
                                   "locked_at = null, last_error = %s where id = %s", (until, detail, cmd.id))
            elif outcome == "stale":
                log.info("dropping stale command %s: %s", cmd.key, detail)
                await conn.execute("update app.outbox set status = 'failed', done_at = %s, last_error = %s, locked_at = null "
                                   "where id = %s", (now, f"stale: {detail}", cmd.id))
            elif cmd.attempts < cmd.max_attempts:
                backoff = dt.timedelta(seconds=self.settings.command_backoff_seconds * 2 ** (cmd.attempts - 1))
                log.warning("command %s attempt %s failed: %s", cmd.key, cmd.attempts, detail)
                await conn.execute("update app.outbox set status = 'pending', run_after = %s, last_error = %s, locked_at = null "
                                   "where id = %s", (now + backoff, detail, cmd.id))
            else:
                log.error("command %s gave up after %s attempts: %s", cmd.key, cmd.attempts, detail)
                await conn.execute("update app.outbox set status = 'failed', done_at = %s, last_error = %s, locked_at = null "
                                   "where id = %s", (now, detail, cmd.id))
                await self._escalate(conn, cmd, detail or "unknown error")

    async def _escalate(self, conn: AsyncConnection, cmd: Command, detail: str) -> None:
        """Send the case to a person with the hold still on (R7)."""
        if cmd.case_id is None:
            return
        state = (await (await conn.execute("select state from app.cases where id = %s", (cmd.case_id,))).fetchone())["state"]
        try:
            if cmd.command == "place_hold" and state == "open":
                await apply_event(conn, cmd.case_id, "hold_failed", f"{cmd.key}:failed",
                                  {"attempts": cmd.attempts, "last_error": detail[:500]})
            else:
                await apply_event(conn, cmd.case_id, "command_failed", f"{cmd.key}:failed",
                                  {"command": cmd.command, "attempts": cmd.attempts, "last_error": detail[:500]})
        except TransitionRefused:
            await conn.execute(
                "insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at) "
                "values (%s, %s, 'command_stuck', 'critical', %s, app.now(%s))",
                (cmd.dataset_id, cmd.case_id, f"Command {cmd.command} failed {cmd.attempts} times in state {state}.", cmd.dataset_id))

    async def _case(self, case_id: UUID | None) -> dict[str, Any]:
        async with system_tx(self.pool) as conn:
            row = await (await conn.execute(CASE, (case_id,))).fetchone()
        if row is None:
            raise Stale(f"case {case_id} not found")
        return row

    @staticmethod
    def _expect(case: dict[str, Any], cmd: Command, states: tuple[str, ...]) -> None:
        version = cmd.payload.get("version")
        if case["state"] not in states or (version is not None and int(version) != case["version"]):
            raise Stale(f"case is {case['state']} at version {case['version']}; command was for "
                        f"{'/'.join(states)} at version {version}")

    async def _emit(self, case: dict[str, Any], event: str, key: str, body: dict[str, Any]) -> None:
        async with system_tx(self.pool) as conn:
            try:
                await apply_event(conn, case["id"], event, key, body)
            except TransitionRefused as exc:
                raise Stale(str(exc)) from exc

    # place_hold (spec 0002, D5) -----------------------------------------------------------------

    async def place_hold(self, cmd: Command) -> None:
        case = await self._case(cmd.case_id)
        self._expect(case, cmd, ("open", "held"))
        ramp = self.connector.for_dataset(case["dataset_id"])
        vendor = case["ramp_vendor_id"]

        schedule = await self._saved_schedule(case["id"])
        if schedule is None:
            # Save every unpaid bill's payment date BEFORE the hold clears them, durably, so a
            # retry never reads the cleared dates.
            bills = await ramp.list_bills_for_vendor(vendor)
            fresh = [{"bill_id": b.id, "payment_date": b.payment_date.isoformat() if b.payment_date else None}
                     for b in bills if not b.paid]
            schedule = await self._save_schedule(case, fresh)

        hold = await ramp.place_vendor_hold(vendor, HOLD_REASON)
        held, bills_held = await self._read_back_hold(ramp, vendor)
        if not held:
            raise Retry("read-back did not show the hold on the vendor and every unpaid bill")
        await self._emit(case, "hold_confirmed", f"{cmd.key}:confirmed",
                         {"readback_ok": True, "ramp_ref": hold.id, "original_schedule": schedule, "bills_held": bills_held})

    async def _saved_schedule(self, case_id: UUID) -> list[dict[str, Any]] | None:
        async with system_tx(self.pool) as conn:
            row = await (await conn.execute(
                "select original_schedule from app.holds where case_id = %s and released_at is null", (case_id,))).fetchone()
        return row["original_schedule"] if row else None

    async def _save_schedule(self, case: dict[str, Any], schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async with system_tx(self.pool) as conn:
            await conn.execute(
                "insert into app.holds (dataset_id, case_id, vendor_id, status, original_schedule, placed_at) "
                "values (%s, %s, %s, 'placing', %s, app.now(%s)) on conflict (case_id) where released_at is null do nothing",
                (case["dataset_id"], case["id"], case["vendor_id"], Jsonb(schedule), case["dataset_id"]))
            row = await (await conn.execute(
                "select original_schedule from app.holds where case_id = %s and released_at is null", (case["id"],))).fetchone()
        return row["original_schedule"]  # whatever was saved first wins

    async def _read_back_hold(self, ramp: RampPort, vendor: str, tries: int = 3) -> tuple[bool, int]:
        for attempt in range(tries):
            v = await ramp.get_vendor(vendor)
            unpaid = [b for b in await ramp.list_bills_for_vendor(vendor) if not b.paid]
            if v.hold_id and all(b.is_on_hold for b in unpaid):
                return True, len(unpaid)
            await self._sleep(0.2 * (attempt + 1))
        return False, 0

    # select_number (D11) -------------------------------------------------------------------------

    async def select_number(self, cmd: Command) -> None:
        case = await self._case(cmd.case_id)
        self._expect(case, cmd, ("held",))
        selection = await self.resolver.select_number(
            case_id=case["id"], dataset_id=case["dataset_id"], vendor_id=case["vendor_id"],
            cutoff=case["ramp_changed_at"] or case["detected_at"])
        if selection.picked:
            body: dict[str, Any] = {"contact_point_id": str(selection.contact_point_id),
                                    "number_selection_id": str(selection.number_selection_id),
                                    "rule_version": selection.rule_version}
            if selection.source_types:
                body["source_types"] = list(selection.source_types)
            await self._emit(case, "number_resolved", f"{cmd.key}:resolved", body)
        else:
            body = {"rule_version": selection.rule_version, "reason": selection.reason or "No number met the rule."}
            if selection.number_selection_id:
                body["number_selection_id"] = str(selection.number_selection_id)
            await self._emit(case, "no_provable_number", f"{cmd.key}:none", body)

    # place_call (D6) ---------------------------------------------------------------------------

    async def _window(self, conn: AsyncConnection, dataset_id: UUID) -> CallingWindow:
        """Demo datasets call within the demo window (spec 0011, D8); every other kind keeps the normal one."""
        row = await (await conn.execute("select kind from app.datasets where id = %s", (dataset_id,))).fetchone()
        return self.demo_window if row is not None and row["kind"] == "demo" else self.window

    async def place_call(self, cmd: Command) -> None:
        case = await self._case(cmd.case_id)
        self._expect(case, cmd, ("calling",))
        attempt = int(cmd.payload["attempt"])
        if case["call_attempts"] != attempt:
            raise Stale(f"case is on attempt {case['call_attempts']}, command was for attempt {attempt}")
        async with system_tx(self.pool) as conn:
            now = await dataset_now(conn, case["dataset_id"])
            window = await self._window(conn, case["dataset_id"])
        if not window.contains(now):
            raise Defer(window.next_open(now), "outside the calling window")
        try:
            call_id = await self.caller.place_call(case_id=case["id"], attempt=attempt, idempotency_key=cmd.key)
        except PreDialRefused as refused:
            # No call was placed and none will be: a person takes over, the hold stays on.
            await self._emit(case, "command_failed", f"{cmd.key}:refused",
                             {"command": "place_call", "attempts": cmd.attempts, "last_error": f"{PRE_DIAL} {refused.code}"})
            return
        await self._emit(case, "call_started", f"{cmd.key}:started", {"call_id": str(call_id), "attempt": attempt})

    # decide (R3) -------------------------------------------------------------------------------

    async def decide(self, cmd: Command) -> None:
        case = await self._case(cmd.case_id)
        self._expect(case, cmd, ("deciding",))
        body = cmd.payload
        answers = (body.get("answers") or {}).get("answers") or []
        async with system_tx(self.pool) as conn:
            generation = await (await conn.execute(GENERATION, (case["id"], case["version"]))).fetchall()
        picks = [e["body"].get("contact_point_id") for e in generation if e["type"] == "number_resolved"]
        # Only this version's pick counts, and a report that doesn't name it isn't eligible (D1).
        dialed_eligible = bool(picks) and body.get("contact_point_id") == picks[-1]
        # Earlier calls can count against the change, never for it (D2).
        earlier = tuple(earlier_call(e["body"]) for e in generation
                        if e["type"] == "call_ended" and e["body"].get("call_id") != body.get("call_id"))
        context = await self.change_context.check(
            case_id=case["id"], dataset_id=case["dataset_id"], vendor_id=case["vendor_id"],
            cutoff=case["ramp_changed_at"] or case["detected_at"],
            speaker_names=values(answers, "speaker_name"), offered_numbers=values(answers, "offered_number"))
        checks = body.get("checks")
        facts = facts_from_answers(
            answers,
            call_result=body["result"],
            dialed_eligible_number=dialed_eligible,
            last_four_result=body.get("last_four_result"),
            company_matches=every_name_matches(values(answers, "company_named"), case["legal_name"], case["trade_names"] or ()),
            checks=CallChecks(**checks) if checks else None,
            earlier_calls=earlier,
            dialed_out=None if body.get("direction") is None else body["direction"] == "outbound",
            dialed_number_is_pick=body.get("dialed_number_is_pick"),
            answered_by=body.get("answered_by"),
            speaker_in_change_thread=context.speaker_in_change_thread,
            offered_number_from_request=context.offered_number_from_request,
            speaker_known_before_request=context.speaker_known_before_request,
        )
        decision = decide(facts)
        await self._emit(case, "decision_made", f"{cmd.key}:made", decision.body())

    # release_hold ------------------------------------------------------------------------------

    async def release_hold(self, cmd: Command) -> None:
        case = await self._case(cmd.case_id)
        self._expect(case, cmd, ("releasing",))
        ramp = self.connector.for_dataset(case["dataset_id"])
        vendor = case["ramp_vendor_id"]
        # Until the case is `released`, any failure puts the hold back before the command retries
        # or gives up, so the vendor is never left unheld in a case that isn't released (spec 0008,
        # D3; R1, R7). That covers a release Ramp did but whose answer was lost, a read-back or a
        # date restore that errors, and a database error on the last step. The next try releases
        # again; placing a hold that is already on changes nothing.
        try:
            await ramp.release_vendor_hold(vendor)
            for attempt in range(3):
                if (await ramp.get_vendor(vendor)).hold_id is None:
                    break
                await self._sleep(0.2 * (attempt + 1))
            else:
                raise Retry("read-back still shows the hold")
            restored = []
            for entry in await self._saved_schedule(case["id"]) or []:
                if entry.get("payment_date"):
                    try:
                        await ramp.set_bill_payment_date(entry["bill_id"], dt.date.fromisoformat(entry["payment_date"]))
                        restored.append(entry)
                    except RampNotFound:
                        log.info("bill %s is paid or gone; not rescheduled", entry["bill_id"])
            await self._emit(case, "hold_release_confirmed", f"{cmd.key}:confirmed",
                             {"readback_ok": True, "restored_schedule": restored})
        except Stale:
            # Another change arrived while we released (the case is held again). Put the hold
            # back at once and always say so: a release must never outlive the case's state.
            back = await self._put_hold_back(ramp, vendor, case["id"])
            async with system_tx(self.pool) as conn:
                await conn.execute(
                    "insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at) "
                    "values (%s, %s, 'vendor_not_held', 'critical', %s, app.now(%s))",
                    (case["dataset_id"], case["id"],
                     "A release raced a new bank change; the hold was put back." if back else
                     "A release raced a new bank change and the hold could not be put back; reconciliation will retry.",
                     case["dataset_id"]))
            raise
        except Exception:
            await self._put_hold_back(ramp, vendor, case["id"])
            raise

    async def _put_hold_back(self, ramp: RampPort, vendor: str, case_id: UUID) -> bool:
        """Best effort: if Ramp is down too, the reconcile job puts it back (invariant I2)."""
        try:
            await ramp.place_vendor_hold(vendor, HOLD_REASON)
            return True
        except Exception as exc:  # noqa: BLE001  (best effort; the failure that got us here still propagates)
            log.error("the hold on case %s could not be put back (%s); reconciliation will", case_id, type(exc).__name__)
            return False

    async def notify(self, cmd: Command) -> None:
        log.info("notify: case %s outcome %s", cmd.case_id, cmd.payload.get("outcome"))

    # Timers ------------------------------------------------------------------------------------

    async def fire_timers(self, dataset_id: UUID | None, limit: int = 20) -> int:
        fired = 0
        for _ in range(limit):
            async with system_tx(self.pool) as conn:
                timer = await (await conn.execute(CLAIM_TIMER, {"dataset": dataset_id})).fetchone()
                if timer is None:
                    return fired
                await self._fire(conn, timer)
            fired += 1
        return fired

    async def _fire(self, conn: AsyncConnection, timer: dict[str, Any]) -> None:
        """Fire one timer. A timer whose case has moved on fires with no action; a refusal is
        logged, never retried forever."""
        now = await dataset_now(conn, timer["dataset_id"])
        case = await (await conn.execute("select id, state, version from app.cases where id = %s", (timer["case_id"],))).fetchone()
        payload = timer["payload"] or {}
        try:
            if timer["kind"] == "retry_call" and case["state"] == "retry_wait" and payload.get("version") in (None, case["version"]):
                window = await self._window(conn, timer["dataset_id"])
                if not window.contains(now):
                    await conn.execute("update app.timers set fire_at = %s where id = %s", (window.next_open(now), timer["id"]))
                    return
                await apply_event(conn, case["id"], "retry_timer_fired",
                                  f"{case['id']}:retry:v{case['version']}:{payload.get('after_attempt')}",
                                  {"in_window": True, "timer_id": timer["id"]})
            elif timer["kind"] == "deadline" and case["state"] in DEADLINE_ACTIVE:
                await apply_event(conn, case["id"], "deadline_reached", f"{case['id']}:deadline",
                                  {"timer_id": timer["id"], "deadline_at": timer["fire_at"].isoformat()})
            elif timer["kind"] == "deadline" and case["state"] != "released":
                # A person owns the case (or it is releasing), so the deadline waits rather than being
                # used up: if a new change hands it back to code, the next check sends it to a person
                # (spec 0007, D4). The hold stays on throughout.
                await conn.execute("update app.timers set fire_at = %s where id = %s", (now + DEADLINE_RECHECK, timer["id"]))
                return
            elif timer["kind"] == "callback":
                # A vendor's callback (spec 0007) is a retry_call timer at the time they asked for; this
                # kind is never scheduled. One firing anyway does nothing rather than call.
                log.warning("unexpected callback timer %s fired with no action", timer["id"])
        except TransitionRefused as exc:
            log.warning("timer %s (%s) refused for case %s: %s", timer["id"], timer["kind"], case["id"], exc)
        await conn.execute("update app.timers set status = 'fired', fired_at = %s where id = %s", (now, timer["id"]))
