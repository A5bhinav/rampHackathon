"""run_relay: drive policy.step() to the end over a Twilio ConversationRelay WebSocket.

It is the same loop as the text line's run_call (transport.py), with two things swapped: the
scripted vendor becomes the WebSocket (incoming `prompt` and `dtmf` messages become policy events,
outgoing `say` lines become `text` tokens), and `connect` becomes the ConversationRelay `setup`
message, which must carry the one-time call_token for exactly this case, attempt and dialed number.

Fail closed, every way (R7). A setup whose token or dialed number does not check closes the socket
and ends the call `failed` with nothing spoken and nothing recorded. A dropped or broken socket mid
call is a Hangup, which step turns into `failed` once consent is on the books, so a recorded "yes"
never becomes a release. The agent is asked through the same `_ask` the text line uses, so a model
error, timeout or empty reply fails closed there too. step() keeps the final say throughout.

The keys (spec 0043, D2) are handled here, because step never reads a clock: digits collect until
'#', '*' starts over, and digits left with no key for KEYPAD_TIMEOUT seconds go in as if '#' were
pressed. Typed digits are never logged or written anywhere; step sees them once, as a Keypad."""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, Protocol

from dialback_policy import step as policy

from .. import tokens as _tokens
from ..agent import Agent
from ..transport import CallResult, Clock, _ask


class WebSocketLike(Protocol):
    """The slice of a WebSocket run_relay uses. A Starlette WebSocket satisfies it directly, and the
    tests pass an in-memory fake. receive_json raises on a dropped connection, as Starlette's does."""
    async def receive_json(self) -> dict[str, Any]: ...
    async def send_json(self, obj: dict[str, Any]) -> None: ...
    async def close(self) -> None: ...


def verify_setup(setup: dict[str, Any], *, key: bytes, case_id: Any, attempt: int, number_e164: str) -> bool:
    """True only if the ConversationRelay `setup` presents the token minted for this exact call and
    was dialed to the number we chose. Either failing means the socket is not the call we placed."""
    params = setup.get("customParameters") or {}
    token = params.get("call_token", "")
    to = setup.get("to") or params.get("to", "")
    return to == number_e164 and _tokens.verify(key, token, case_id=case_id, attempt=attempt,
                                                 number_e164=number_e164)


def map_message(msg: dict[str, Any], at: dt.datetime) -> policy.Event | None:
    """One ConversationRelay message to one policy event, or None for a message that drives no event
    on its own: an interim transcription, an `info`/`interrupt`, an unknown type, or a single `dtmf`
    keypress (ConversationRelay sends one per key, so run_relay collects those; see `_Inbox`). A
    final `prompt` is the callee's words, or Silence when it carries none."""
    if msg.get("type") == "prompt":
        if not msg.get("last", True):
            return None  # a partial transcription; act only on the final one
        said = (msg.get("voicePrompt") or "").strip()
        return policy.Silence(at) if not said else policy.CalleeSaid(at, said)
    return None


KEYPAD_TIMEOUT = 5.0  # seconds with no key, once a digit is typed, before the entry goes in (spec 0043, Q78)
MAX_KEYS = 16         # digits held per entry; later ones are dropped, the entry still goes in, and step refuses it


async def run_relay(ctx: policy.CallContext, agent: Agent, ws: WebSocketLike, *,
                    verify: Any, clock: Clock, greeting: str | None = None,
                    keypad_timeout: float = KEYPAD_TIMEOUT, max_steps: int = 500) -> CallResult:
    """Read the setup, verify it, then run step() to its End over the socket. Mirrors run_call.

    `greeting`, when set, is a line ConversationRelay has already spoken as the welcomeGreeting (the
    disclosure): step still produces and records it, but it is not sent again over the socket, so the
    callee hears it once (R8). `keypad_timeout` is the wait after the last key; tests shorten it."""
    setup = await ws.receive_json()
    if setup.get("type") != "setup" or not verify(setup):
        await ws.close()                                     # a stray or cross-wired socket (R7)
        return _failed(ctx, "setup_rejected")

    state = policy.start(ctx)
    event: policy.Event = policy.Connected(clock(), "human")
    inbox = _Inbox(ws, keypad_timeout)
    try:
        for _ in range(max_steps):
            out = policy.step(state, event)
            state = out.state
            if out.say:
                await _send(ws, out.say, greeting)
            if out.end is not None:
                return CallResult(end=out.end, answers=policy.answers_object(state),
                                  checks=policy.checks(state), transcript=state.turns)
            if out.ask_agent is not None:
                event = _ask(agent, out.ask_agent, clock())
                continue
            event = await inbox.next_event(clock)
    finally:
        inbox.close()
    raise RuntimeError("the relay did not reach an end within max_steps")  # step caps turns; a guard


async def _send(ws: WebSocketLike, lines: tuple[str, ...], greeting: str | None = None) -> None:
    for line in lines:
        if greeting is not None and line == greeting:
            continue  # ConversationRelay already spoke this as the welcomeGreeting (R8); don't repeat it
        await ws.send_json({"type": "text", "token": line, "last": True})


class _Inbox:
    """The socket's messages as policy events. ConversationRelay sends one `dtmf` message per key, so
    digits collect here into one entry (spec 0043, D2): '#' sends it as a Keypad, '*' empties it and
    is a KeypadReset, and digits left with no key for `timeout` seconds go in as if '#' were pressed.
    There is no submit at four keys, and at most MAX_KEYS digits are held. A spoken turn or a silence
    ends a partial entry. A receive that raises is a dropped or broken call: a Hangup, which step
    fails closed once consent is recorded (R7). At most one receive is in flight, and a timeout leaves
    it running for the next event, so no message is ever lost to a cancelled read."""

    def __init__(self, ws: WebSocketLike, timeout: float) -> None:
        self._ws = ws
        self._timeout = timeout
        self._digits: list[str] = []
        self._deadline = 0.0                                 # loop time the entry goes in by, once typed
        self._pending: asyncio.Future[dict[str, Any]] | None = None

    async def next_event(self, clock: Clock) -> policy.Event:
        loop = asyncio.get_running_loop()
        while True:
            wait = max(0.0, self._deadline - loop.time()) if self._digits else None
            try:
                msg = await self._receive(wait)
            except Exception:  # noqa: BLE001  any receive failure is a dead socket, never a live call
                return policy.Hangup(clock())
            if msg is None:                                  # no key in time: the entry goes in as typed
                return self._submit(clock)
            if msg.get("type") == "dtmf":
                key = (msg.get("digit") or msg.get("digits") or "").strip()
                if key == "#":                               # the caller signals the end of entry
                    return self._submit(clock)
                if key == "*":                               # start over: what was typed is gone
                    self._digits.clear()
                    return policy.KeypadReset(clock())
                if key.isdigit():
                    self._digits.extend(key[:MAX_KEYS - len(self._digits)])  # past MAX_KEYS, dropped
                    self._deadline = loop.time() + self._timeout
                continue                                     # noise: keep collecting keypresses
            event = map_message(msg, clock())
            if event is not None:
                self._digits.clear()                         # a spoken turn ends a partial keypad entry
                return event

    async def _receive(self, timeout: float | None) -> dict[str, Any] | None:
        """The next message, or None when `timeout` seconds pass first. Raises what the socket raises."""
        if self._pending is None:
            self._pending = asyncio.ensure_future(self._ws.receive_json())
        done, _ = await asyncio.wait({self._pending}, timeout=timeout)
        if not done:
            return None
        task, self._pending = self._pending, None
        return task.result()

    def _submit(self, clock: Clock) -> policy.Keypad:
        digits = "".join(self._digits)
        self._digits.clear()
        return policy.Keypad(clock(), digits)

    def close(self) -> None:
        if self._pending is not None:
            self._pending.cancel()                           # the call is over; nothing is waiting for it
            self._pending = None


def _failed(ctx: policy.CallContext, reason: str) -> CallResult:
    """A call that failed before any turn: failed result, nothing recorded, no transcript."""
    state = policy.start(ctx)
    end = policy.End(result="failed", reason=f"relay:{reason}")
    return CallResult(end=end, answers=policy.answers_object(state), checks=policy.checks(state),
                      transcript=state.turns)
