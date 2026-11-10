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
pressed. Typed digits are never logged or written anywhere; step sees them once, as a Keypad.

So is the quiet clock (spec 0051), for the same reason. Each time the relay waits for the caller it
arms the clock: after a step that spoke, it starts when Twilio reports the agent stopped speaking,
or at an estimate of when the lines finish if no such report comes; after a step that said nothing,
at once. Activity from the caller (a prompt, partial or final, even an empty one; a key; Twilio's
clientSpeaking) postpones it, and nothing stops it for good. When the quiet has lasted from the
latest activity, the relay hands step a Silence, and step checks in or ends the call closed. Code,
never the model, decides what counts as activity (R3). While digits are held, the keypad wait
governs instead (D7)."""
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol

from dialback_policy import step as policy
from dialback_policy.script import Phase

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
    on its own: an interim transcription, an empty final one, an `info`/`interrupt`, an unknown type,
    or a single `dtmf` keypress (ConversationRelay sends one per key, so run_relay collects those; see
    `_Inbox`). A final `prompt` with words is the callee's words. An empty one is line noise: activity
    for the quiet clock, never a Silence (spec 0051, D2)."""
    if msg.get("type") == "prompt":
        if not msg.get("last", True):
            return None  # a partial transcription; act only on the final one
        said = (msg.get("voicePrompt") or "").strip()
        return policy.CalleeSaid(at, said) if said else None
    return None


KEYPAD_TIMEOUT = 10.0  # seconds after the last kept digit before the entry goes in (spec 0043, Q78, revised Nov 9)
MAX_KEYS = 16         # digits held per entry; later ones are dropped, the entry still goes in, and step refuses it

# The quiet clock (spec 0051, D1): seconds of quiet before a Silence. The digits question waits
# longer, since finding a statement takes time.
QUIET_SECONDS = 10.0                 # after the agent finishes, before the check-in
QUIET_SECONDS_LAST4 = 20.0           # the same at the digits question
AFTER_CHECK_IN_SECONDS = 10.0        # after a check-in, before the call ends
AFTER_CHECK_IN_SECONDS_LAST4 = 20.0  # the same after the digits check-in

# When the lines finish playing, if Twilio does not say (spec 0051, D3; Q118): 1 second, plus 1
# second per 12 characters of every line the step said, plus 3 seconds of margin.
SPEECH_LEAD_SECONDS = 1.0
SPEECH_CHARS_PER_SECOND = 12.0
SPEECH_MARGIN_SECONDS = 3.0


@dataclass(frozen=True)
class SpeechEstimate:
    """How long a step's lines take to play, counted from the moment the last one was sent. It counts
    every line the step said, the welcome greeting included, and errs late: a check-in can come a
    little late, never early. Tests inject shorter constants."""
    lead: float = SPEECH_LEAD_SECONDS
    chars_per_second: float = SPEECH_CHARS_PER_SECOND
    margin: float = SPEECH_MARGIN_SECONDS

    def seconds(self, lines: tuple[str, ...]) -> float:
        return self.lead + sum(len(line) for line in lines) / self.chars_per_second + self.margin


SPEECH_ESTIMATE = SpeechEstimate()


async def run_relay(ctx: policy.CallContext, agent: Agent, ws: WebSocketLike, *,
                    verify: Any, clock: Clock, greeting: str | None = None,
                    keypad_timeout: float = KEYPAD_TIMEOUT, quiet_seconds: float = QUIET_SECONDS,
                    quiet_seconds_last4: float = QUIET_SECONDS_LAST4,
                    after_check_in_seconds: float = AFTER_CHECK_IN_SECONDS,
                    after_check_in_seconds_last4: float = AFTER_CHECK_IN_SECONDS_LAST4,
                    estimate: SpeechEstimate = SPEECH_ESTIMATE, max_steps: int = 500) -> CallResult:
    """Read the setup, verify it, then run step() to its End over the socket. Mirrors run_call.

    `greeting`, when set, is a line ConversationRelay has already spoken as the welcomeGreeting (the
    disclosure): step still produces and records it, but it is not sent again over the socket, so the
    callee hears it once (R8). `keypad_timeout` is the wait after the last key; the quiet timings and
    the speech `estimate` set the quiet clock (spec 0051). Tests shorten them all."""
    setup = await ws.receive_json()
    if setup.get("type") != "setup" or not verify(setup):
        await ws.close()                                     # a stray or cross-wired socket (R7)
        return _failed(ctx, "setup_rejected")

    def quiet_limit(state: policy.State) -> float:
        """The seconds of quiet this wait allows: longer at the digits question, and its own after a
        check-in (spec 0051, D1)."""
        last4 = state.phase is Phase.LAST4
        if state.check_in_pending:
            return after_check_in_seconds_last4 if last4 else after_check_in_seconds
        return quiet_seconds_last4 if last4 else quiet_seconds

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
            inbox.arm(out.say, quiet_limit(state), estimate)
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
    is a KeypadReset, and digits left with no kept key for `timeout` seconds go in as if '#' were
    pressed. There is no submit at four keys, and at most MAX_KEYS digits are held; a digit past that
    is dropped and does not restart the wait. A spoken turn ends a partial entry. A receive that
    raises is a dropped or broken call: a Hangup, which step fails closed once consent is recorded
    (R7). At most one receive is in flight, and a timeout leaves it running for the next event, so no
    message is ever lost to a cancelled read.

    With no digits held, the quiet clock armed by `arm` bounds every wait (spec 0051): when it runs
    out, the event is a Silence. A message that is not a JSON object, an `info` this does not know,
    or a malformed one is ignored: it is not activity, and it neither starts nor stops the clock."""

    def __init__(self, ws: WebSocketLike, timeout: float) -> None:
        self._ws = ws
        self._timeout = timeout
        self._digits: list[str] = []
        self._deadline = 0.0                                 # loop time the entry goes in by, once typed
        self._pending: asyncio.Future[dict[str, Any]] | None = None
        # The quiet clock, re-armed for every wait (spec 0051, D3). All loop times.
        self._limit = 0.0                                    # seconds of quiet this wait allows
        self._spoke = False                                  # the step before this wait said something
        self._estimate_at = 0.0                              # when the estimate says the lines finish
        self._started: float | None = None                   # when the clock started, once known
        self._agent_on = False                               # agentSpeaking "on" seen since the last send
        self._activity = float("-inf")                       # the caller's latest activity

    def arm(self, said: tuple[str, ...], limit: float, estimate: SpeechEstimate) -> None:
        """Start a new wait, just after the step's lines were sent. After a step that spoke, the clock
        starts when Twilio says the agent stopped, or at the estimate if it never says; after a step
        that said nothing, at once (spec 0051, D3)."""
        now = asyncio.get_running_loop().time()
        self._limit = limit
        self._spoke = bool(said)
        self._estimate_at = now + estimate.seconds(said) if said else now
        self._started = None if said else now
        self._agent_on = False
        self._activity = float("-inf")

    def _quiet_deadline(self) -> float:
        start = self._started if self._started is not None else self._estimate_at
        return max(start, self._activity) + self._limit    # measured from the latest activity (D2)

    def _info(self, msg: dict[str, Any], now: float) -> None:
        """Twilio's speaker events (Q117): {"type": "info", "name": ..., "value": "on" | "off"}. Any
        other `info`, or a malformed one, changes nothing."""
        name, value = msg.get("name"), msg.get("value")
        if value not in ("on", "off"):
            return
        if name == "clientSpeaking":                         # the caller is speaking: activity (D2)
            self._activity = now
        elif name == "agentSpeaking" and self._spoke:
            if value == "on":
                self._agent_on = True
            elif self._agent_on:                             # only an "off" after an "on" since the send
                self._started = now

    async def next_event(self, clock: Clock) -> policy.Event:
        loop = asyncio.get_running_loop()
        while True:
            deadline = self._deadline if self._digits else self._quiet_deadline()   # keys govern (D7)
            try:
                msg = await self._receive(max(0.0, deadline - loop.time()))
            except Exception:  # noqa: BLE001  any receive failure is a dead socket, never a live call
                return policy.Hangup(clock())
            if msg is None:
                if self._digits:                             # no key in time: the entry goes in as typed
                    return self._submit(clock)
                return policy.Silence(clock())               # the quiet lasted: step checks in or ends
            if not isinstance(msg, dict):
                continue                                     # not a message at all: ignored
            now = loop.time()
            kind = msg.get("type")
            if kind == "info":
                self._info(msg, now)
                continue
            if kind in ("prompt", "dtmf"):
                self._activity = now                         # speech, even empty or partial, or a key (D2)
            if kind == "dtmf":
                key = (msg.get("digit") or msg.get("digits") or "").strip()
                if key == "#":                               # the caller signals the end of entry
                    return self._submit(clock)
                if key == "*":                               # start over: what was typed is gone
                    self._digits.clear()
                    return policy.KeypadReset(clock())
                room = MAX_KEYS - len(self._digits)
                if key.isdigit() and room > 0:               # past MAX_KEYS a digit is dropped and does
                    self._digits.extend(key[:room])          # not restart the wait, so the cap bounds time too
                    self._deadline = loop.time() + self._timeout
                continue                                     # noise: keep collecting keypresses
            event = map_message(msg, clock())
            if event is not None:
                self._digits.clear()                         # a spoken turn ends a partial keypad entry
                return event

    async def _receive(self, timeout: float) -> dict[str, Any] | None:
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
