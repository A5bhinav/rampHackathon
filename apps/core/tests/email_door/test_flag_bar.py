"""The flagging pass bar (design section 3; spec 0086): in each half of the labeled emails, every change
request is flagged and at most 3 ordinary emails are. The held-out half is sealed by its SHA-256 and is
run only once the rule is final."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dialback_core.email_door.rules import flag

FIXTURES = Path(__file__).parent / "fixtures"
MAX_ORDINARY_FLAGGED = 3  # an absolute count, set before any tuning; never loosen it


def load(half: str) -> dict[str, dict]:
    return {p.name: json.loads(p.read_text()) for p in sorted((FIXTURES / half).glob("*.json"))}


def check_bar(emails: dict[str, dict], change_requests: int, ordinary: int) -> None:
    labels = [e["label"] for e in emails.values()]
    assert (labels.count("change_request"), labels.count("ordinary"), len(labels)) == (change_requests, ordinary, change_requests + ordinary)
    flagged = {name: flag(e["subject"], e["text"]).flagged for name, e in emails.items()}
    missed = sorted(n for n, e in emails.items() if e["label"] == "change_request" and not flagged[n])
    false_flags = sorted(n for n, e in emails.items() if e["label"] == "ordinary" and flagged[n])
    summary = (f"change requests flagged {change_requests - len(missed)}/{change_requests} (missed: {missed}); "
               f"ordinary flagged {len(false_flags)}/{ordinary} (flagged: {false_flags})")
    assert not missed and len(false_flags) <= MAX_ORDINARY_FLAGGED, summary


def test_tuning_half_meets_the_bar():
    check_bar(load("tuning"), change_requests=30, ordinary=62)


def test_heldout_half_meets_the_bar():
    files = sorted((FIXTURES / "heldout").glob("*.json"))
    sealed = (FIXTURES / "heldout.sha256").read_text().split()[0]
    assert hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest() == sealed
    check_bar(load("heldout"), change_requests=35, ordinary=65)
