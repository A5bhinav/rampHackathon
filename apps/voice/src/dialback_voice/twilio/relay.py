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
the start still pending from the last lines sent carries over (at once, if none is). Activity from
the caller (a prompt, partial or final, even an empty one; a key; Twilio's clientSpeaking) postpones
it, and a caller mid-sentence holds it, but never past QUIET_CAP_FACTOR waits, so nothing keeps a
wait open for good. When the quiet has lasted from the latest activity, the relay hands step a
Silence, and step checks in or ends the call closed. Code, never the model, decides what counts as
activity (R3). While digits are held, the keypad wait governs instead (D7).

At the end (spec 0094) the call is reported first, through `on_end`, so the decision is not held
back. The relay then waits for the closing line to play, until Twilio says the agent stopped or
the speech estimate runs out, and sends ConversationRelay's `end`, so Twilio ends the call on
purpose instead of losing the socket mid-sentence."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from dialback_policy import step as policy

from .. import tokens as _tokens
from ..agent import Agent
from ..transport import CallResult, Clock, _ask

log = logging.getLogger(__name__)


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
# Activity postpones the clock, and a caller mid-sentence holds it, but never past this many quiet
# waits from the moment the clock could first start, so nothing keeps a wait open for good.
QUIET_CAP_FACTOR = 3

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

# After Twilio says the closing line stopped, this long before the end, so network delay never
# clips the last word (spec 0094, D2). Without that report, the speech estimate is the wait.
END_TAIL_SECONDS = 0.5


async def run_relay(ctx: policy.CallContext, agent: Agent, ws: WebSocketLike, *,
                    verify: Any, clock: Clock, greeting: str | None = None,
                    keypad_timeout: float = KEYPAD_TIMEOUT, quiet_seconds: float = QUIET_SECONDS,
                    quiet_seconds_last4: float = QUIET_SECONDS_LAST4,
                    after_check_in_seconds: float = AFTER_CHECK_IN_SECONDS,
                    after_check_in_seconds_last4: float = AFTER_CHECK_IN_SECONDS_LAST4,
                    estimate: SpeechEstimate = SPEECH_ESTIMATE, max_steps: int = 500,
                    on_end: Callable[[CallResult], Awaitable[None]] | None = None) -> CallResult:
    """Read the setup, verify it, then run step() to its End over the socket. Mirrors run_call.

    `greeting`, when set, is a line ConversationRelay has already spoken as the welcomeGreeting (the
    disclosure): step still produces and records it, but it is not sent again over the socket, so the
    callee hears it once (R8). `keypad_timeout` is the wait after the last key; the quiet timings and
    the speech `estimate` set the quiet clock (spec 0051). Tests shorten them all.

    `on_end`, when set, is awaited with every result this returns, before any waiting: the caller
    reports the call there (spec 0094, D1). After an End that spoke a closing line, the relay then
    waits for the line to play and sends ConversationRelay's `end`, even when `on_end` raises; the
    error then propagates (D5)."""
    setup = await _setup_message(ws)
    if not verify(setup):
        await ws.close()                                     # a stray or cross-wired socket (R7)
        rejected = _failed(ctx, "setup_rejected")
        if on_end is not None:
            await on_end(rejected)                           # reported, with nothing spoken and no end (D6)
        return rejected

    def quiet_limit(state: policy.State) -> float:
        """The seconds of quiet this wait allows: longer while the digits question is the open one,
        and its own after a check-in (spec 0051, D1)."""
        last4 = policy.asking_digits(state)
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
            spoken = True
            if out.say:
                try:
                    await _send(ws, out.say, greeting)
                except Exception:                            # mid-call this propagates, as before
                    if out.end is None:
                        raise
                    spoken = False                           # hung up at the closing line: still report (D7)
            if out.end is not None:
                result = CallResult(end=out.end, answers=policy.answers_object(state),
                                    checks=policy.checks(state), transcript=state.turns)
                closing = tuple(line for line in out.say or () if line != greeting) if spoken else ()
                if closing:
                    inbox.arm(closing, 0.0, estimate)        # the estimate counts from the send (spec 0094, D2)
                try:
                    if on_end is not None:
                        await on_end(result)                 # the report goes out before any waiting (D1)
                finally:
                    if closing and await inbox.finish(END_TAIL_SECONDS):
                        await _end_session(ws)               # only a socket still up gets the end (D4)
                return result
            if out.ask_agent is not None:
                event = _ask(agent, out.ask_agent, clock())
                continue
            inbox.arm(out.say, quiet_limit(state), estimate)
            event = await inbox.next_event(clock)
    finally:
        inbox.close()
    raise RuntimeError("the relay did not reach an end within max_steps")  # step caps turns; a guard


async def _setup_message(ws: WebSocketLike) -> dict[str, Any]:
    """The ConversationRelay `setup` message. Anything before it (a speaker event, now that those are
    subscribed, or anything malformed) is ignored; only a setup that does not verify rejects the
    socket. A receive that raises propagates, as before."""
    while True:
        msg = await ws.receive_json()
        if isinstance(msg, dict) and msg.get("type") == "setup":
            return msg


async def _send(ws: WebSocketLike, lines: tuple[str, ...], greeting: str | None = None) -> None:
    for line in lines:
        if greeting is not None and line == greeting:
            continue  # ConversationRelay already spoke this as the welcomeGreeting (R8); don't repeat it
        await ws.send_json({"type": "text", "token": line, "last": True})


async def _end_session(ws: WebSocketLike) -> None:
    """ConversationRelay's `end`: Twilio ends the session, and with no TwiML after it, the call. A
    send that fails changes nothing: the call is over either way (spec 0094, D4)."""
    try:
        await ws.send_json({"type": "end"})
    except Exception:  # noqa: BLE001  the far end is already gone
        log.info("the end message did not send; the caller had already hung up")


_TIMED_OUT = object()   # what _Inbox._receive returns when a wait runs out: never a message


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
    out, the event is a Silence. A caller mid-sentence (a clientSpeaking "on" with no "off" yet) holds
    it, and QUIET_CAP_FACTOR waits after it could first start it fires whatever came in between. A
    message that is not a JSON object (a null included), an `info` this does not know, or a malformed
    one is ignored: it is not activity, and it neither starts nor stops the clock."""

    def __init__(self, ws: WebSocketLike, timeout: float) -> None:
        self._ws = ws
        self._timeout = timeout
        self._digits: list[str] = []
        self._deadline = 0.0                                 # loop time the entry goes in by, once typed
        self._pending: asyncio.Future[dict[str, Any]] | None = None
        # The quiet clock (spec 0051, D3). All loop times.
        self._limit = 0.0                                    # seconds of quiet this wait allows
        self._armed_at = 0.0                                 # the clock never starts before its wait began
        self._estimate_at = 0.0                              # when the estimate says the last lines finish
        self._stopped_at: float | None = None                # when Twilio said the agent stopped, since the send
        self._agent_on = False                               # agentSpeaking "on" seen since the last send
        self._client_on = False                              # clientSpeaking "on" with no "off": mid-sentence
        self._activity = float("-inf")                       # the caller's latest activity in this wait
        self._cap_at = 0.0                                   # when this wait ends, whatever comes

    def arm(self, said: tuple[str, ...], limit: float, estimate: SpeechEstimate) -> None:
        """Start a new wait, just after the step's lines were sent (spec 0051, D3). After a step that
        spoke, the clock starts when Twilio says the agent stopped, or at the estimate if it never
        says. After a step that said nothing (a '#' or '*' while the question still plays, say), the
        start still pending from the last lines sent carries over, so a key cannot bring the check-in
        early; with nothing pending, it starts at once. The cap counts from the later of now and the
        estimate, so a long line never eats into it."""
        now = asyncio.get_running_loop().time()
        self._limit = limit
        self._armed_at = now
        if said:
            self._estimate_at = now + estimate.seconds(said)
            self._stopped_at = None
            self._agent_on = False
            self._client_on = False
        self._activity = float("-inf")
        self._cap_at = max(now, self._estimate_at) + QUIET_CAP_FACTOR * limit

    def _quiet_deadline(self) -> float:
        pending = self._stopped_at if self._stopped_at is not None else self._estimate_at
        start = max(self._armed_at, pending)
        if self._client_on:
            return self._cap_at                              # the caller is mid-sentence: hold, up to the cap
        return min(max(start, self._activity) + self._limit, self._cap_at)   # from the latest activity (D2)

    def _info(self, msg: dict[str, Any], now: float) -> None:
        """Twilio's speaker events (Q117): {"type": "info", "name": ..., "value": "on" | "off"}. Any
        other `info`, or a malformed one, changes nothing."""
        name, value = msg.get("name"), msg.get("value")
        if value not in ("on", "off"):
            return
        if name == "clientSpeaking":                         # the caller is speaking: activity (D2)
            self._activity = now
            self._client_on = value == "on"
        elif name == "agentSpeaking":
            if value == "on":
                self._agent_on = True
            elif self._agent_on:                             # only an "off" after an "on" since the send
                self._stopped_at = now

    async def next_event(self, clock: Clock) -> policy.Event:
        loop = asyncio.get_running_loop()
        while True:
            deadline = self._deadline if self._digits else self._quiet_deadline()   # keys govern (D7)
            try:
                msg = await self._receive(max(0.0, deadline - loop.time()))
            except Exception:  # noqa: BLE001  any receive failure is a dead socket, never a live call
                return policy.Hangup(clock())
            if msg is _TIMED_OUT:
                if self._digits:                             # no key in time: the entry goes in as typed
                    return self._submit(clock)
                return policy.Silence(clock())               # the quiet lasted: step checks in or ends
            if not isinstance(msg, dict):
                continue                                     # not a message at all, a null included: ignored
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

    async def finish(self, tail: float) -> bool:
        """Wait for the closing lines armed by `arm` to play (spec 0094, D2): until Twilio says the
        agent stopped (an "off" after an "on" since the send) plus `tail`, or until the estimate,
        whichever comes first. Everything else that arrives meanwhile, a prompt or a key included,
        is dropped: the call has ended (D3). True if the wait ran out with the socket still up, False
        if a receive failed (the caller hung up first)."""
        loop = asyncio.get_running_loop()
        while True:
            deadline = self._estimate_at
            if self._stopped_at is not None:
                deadline = min(deadline, self._stopped_at + tail)
            try:
                msg = await self._receive(max(0.0, deadline - loop.time()))
            except Exception:  # noqa: BLE001  a dead socket: nothing more to send
                log.info("closing line: the wait ended (dropped)")
                return False
            if msg is _TIMED_OUT:
                # Which ended it, for tuning from live calls (spec 0094, D8). No content, no number.
                why = "agent_stopped" if self._stopped_at is not None and deadline < self._estimate_at else "estimate"
                log.info("closing line: the wait ended (%s)", why)
                return True
            if isinstance(msg, dict) and msg.get("type") == "info":
                self._info(msg, loop.time())

    async def _receive(self, timeout: float) -> Any:
        """The next message, or _TIMED_OUT when `timeout` seconds pass first. Raises what the socket
        raises. A JSON null is a message (None), never the timeout."""
        if self._pending is None:
            self._pending = asyncio.ensure_future(self._ws.receive_json())
        done, _ = await asyncio.wait({self._pending}, timeout=timeout)
        if not done:
            return _TIMED_OUT
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
