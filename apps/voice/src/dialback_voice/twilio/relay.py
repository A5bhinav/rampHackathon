"""run_relay: drive policy.step() to the end over a Twilio ConversationRelay WebSocket.

It is the same loop as the text line's run_call (transport.py), with two things swapped: the
scripted vendor becomes the WebSocket (incoming `prompt` and `dtmf` messages become policy events,
outgoing `say` lines become `text` tokens), and `connect` becomes the ConversationRelay `setup`
message, which must carry the one-time call_token for exactly this case, attempt and dialed number.

Fail closed, every way (R7). A setup whose token or dialed number does not check closes the socket
and ends the call `failed` with nothing spoken and nothing recorded. A dropped or broken socket mid
call is a Hangup, which step turns into `failed` once consent is on the books, so a recorded "yes"
never becomes a release. The agent is asked through the same `_ask` the text line uses, so a model
error, timeout or empty reply fails closed there too. step() keeps the final say throughout."""
from __future__ import annotations

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
    (an interim transcription, an `info`/`interrupt`, or an unknown type), which run_relay skips."""
    kind = msg.get("type")
    if kind == "prompt":
        if not msg.get("last", True):
            return None  # a partial transcription; act only on the final one
        said = (msg.get("voicePrompt") or "").strip()
        return policy.Silence(at) if not said else policy.CalleeSaid(at, said)
    if kind == "dtmf":
        return policy.Keypad(at, msg.get("digit") or msg.get("digits") or "")
    return None


async def run_relay(ctx: policy.CallContext, agent: Agent, ws: WebSocketLike, *,
                    verify: Any, clock: Clock, max_steps: int = 500) -> CallResult:
    """Read the setup, verify it, then run step() to its End over the socket. Mirrors run_call."""
    setup = await ws.receive_json()
    if setup.get("type") != "setup" or not verify(setup):
        await ws.close()                                     # a stray or cross-wired socket (R7)
        return _failed(ctx, "setup_rejected")

    state = policy.start(ctx)
    event: policy.Event = policy.Connected(clock(), "human")
    for _ in range(max_steps):
        out = policy.step(state, event)
        state = out.state
        if out.say:
            await _send(ws, out.say)
        if out.end is not None:
            return CallResult(end=out.end, answers=policy.answers_object(state),
                              checks=policy.checks(state), transcript=state.turns)
        if out.ask_agent is not None:
            event = _ask(agent, out.ask_agent, clock())
            continue
        event = await _next_event(ws, clock)
    raise RuntimeError("the relay did not reach an end within max_steps")  # step caps turns; a guard


async def _send(ws: WebSocketLike, lines: tuple[str, ...]) -> None:
    for line in lines:
        await ws.send_json({"type": "text", "token": line, "last": True})


async def _next_event(ws: WebSocketLike, clock: Clock) -> policy.Event:
    """The next event from the socket, skipping messages that map to nothing. A receive that raises
    is a dropped or broken call: a Hangup, which step fails closed once consent is recorded (R7)."""
    while True:
        try:
            msg = await ws.receive_json()
        except Exception:  # noqa: BLE001  any receive failure is a dead socket, never a live call
            return policy.Hangup(clock())
        event = map_message(msg, clock())
        if event is not None:
            return event


def _failed(ctx: policy.CallContext, reason: str) -> CallResult:
    """A call that failed before any turn: failed result, nothing recorded, no transcript."""
    state = policy.start(ctx)
    end = policy.End(result="failed", reason=f"relay:{reason}")
    return CallResult(end=end, answers=policy.answers_object(state), checks=policy.checks(state),
                      transcript=state.turns)
