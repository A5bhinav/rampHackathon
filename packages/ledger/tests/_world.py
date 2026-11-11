"""Builds select() inputs for the tests: hand-made worlds and the frozen corpus."""
import contextlib
import datetime as dt
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

from dialback_ledger.select import ChangeRequest, DocumentRef, Sighting, select

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "packages/contracts/schemas/selection.schema.json").read_text())
T0 = dt.datetime(2026, 10, 1, 16, 0, tzinfo=dt.UTC)
CASE, DATASET, VENDOR = (uuid.UUID(int=n) for n in (1, 2, 3))
REQUEST = ChangeRequest(document_id="req", received_at=T0, thread_id="req", subject="Updated banking details",
                        sender="ar@acme.example")


def at(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def world(rows: list[dict], *, opened: dt.datetime = T0 + dt.timedelta(hours=3)) -> dict:
    """One document per row. A row: e164, channel, days_before, visibility, role, party, thread,
    subject, lookalike, and optionally recorded_late (recorded after the case opened)."""
    docs, sightings = [DocumentRef("req", "email", T0, "ar@acme.example", "req", "Updated banking details", T0)], []
    for i, r in enumerate(rows):
        received = T0 - dt.timedelta(days=r["days_before"])
        recorded = opened + dt.timedelta(days=1) if r.get("recorded_late") else min(received, opened - dt.timedelta(seconds=1))
        doc_id = r.get("doc_id", f"d{i}")
        sender = f"ar@{r.get('domain') or ('acrne.example' if r.get('lookalike') else 'acme.example')}"
        docs.append(DocumentRef(doc_id, r["channel"], received, sender, r.get("thread"), r.get("subject", "Hello"), recorded))
        sightings.append(Sighting(r["e164"], doc_id, r["channel"], received, r["visibility"], r["role"], r["party"], recorded))
    return {"case_id": CASE, "dataset_id": DATASET, "vendor_id": VENDOR, "vendor_domains": ("acme.example",),
            "request": REQUEST, "requested_cutoff": T0 + dt.timedelta(hours=2), "case_opened_at": opened,
            "documents": docs, "sightings": sightings}


def provable(e164: str, days: int, **kw) -> list[dict]:
    """Two counting sources, `days` before the request: provable on its own."""
    return [{"e164": e164, "channel": ch, "days_before": days, "visibility": "visible", "role": "ar", "party": "vendor"} | kw
            for ch in ("contract", "listing")]


def in_thread(e164: str, days: int = 0, role: str = "ar", party: str = "vendor", **kw) -> dict:
    """A sighting in the change request's thread: change context."""
    return {"e164": e164, "channel": "email", "days_before": days, "visibility": "visible", "role": role, "party": party,
            "thread": "req", "subject": "Re: Updated banking details"} | kw


def run(w: dict) -> dict:
    return select(**w)


def corpus_world(truth_path: Path) -> tuple[dict, dict]:
    truth = json.loads(truth_path.read_text())
    raw = json.loads((ROOT / "corpus" / truth["documents"]).read_text())
    docs = [DocumentRef(d["id"], d["custody"]["channel"], at(d["custody"]["received_at"]), d["custody"]["sender"],
                        d["custody"]["thread_id"], d["custody"]["subject"], at(d["custody"]["received_at"])) for d in raw]
    sightings = [Sighting(p["e164"], s["document_id"], s["channel"], at(s["received_at"]), s["visibility"], s["role"],
                          s["party"], at(s["received_at"])) for p in truth["planted"] for s in p["sightings"]]
    req = next(d for d in docs if d.id == truth["request_document_id"])
    ramp = at(truth["ramp_change_at"])
    return truth, {"case_id": CASE, "dataset_id": DATASET, "vendor_id": VENDOR,
                   "vendor_domains": (truth["vendor"]["domain"],),
                   "request": ChangeRequest(req.id, req.received_at, req.thread_id, req.subject, req.sender),
                   "requested_cutoff": ramp, "case_opened_at": ramp + dt.timedelta(hours=1),
                   "documents": docs, "sightings": sightings}


def _frozen_module(name: str):
    """A frozen copy in tests/fixtures/, as main had it when spec 0079 began."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "fixtures" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the class is built
    spec.loader.exec_module(module)
    return module


_frozen_module("lookalike_v1_frozen")       # the classifier select_v1_frozen imports by this name
FROZEN = _frozen_module("select_v1_frozen")


def frozen(w: dict) -> dict:
    """The frozen copy of today's rule on a world (spec 0079's baseline)."""
    return FROZEN.select(**w)


def calls(out: dict) -> list[str]:
    """What a selection would call, in order: the pick, then its alternates (spec 0079's call list)."""
    return ([out["e164"]] if out.get("e164") else []) + [a["e164"] for a in out.get("alternates", [])]


@contextlib.contextmanager
def flags(guard: str | None = None, bar: str | None = None):
    """DIALBACK_CONTEXT_GUARD and DIALBACK_LABEL_BAR for the block (None: unset, the default), then as
    they were. A context manager rather than monkeypatch, so Hypothesis tests can use it too."""
    names = {"DIALBACK_CONTEXT_GUARD": guard, "DIALBACK_LABEL_BAR": bar}
    before = {name: os.environ.get(name) for name in names}

    def put(values: dict) -> None:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    try:
        put(names)
        yield
    finally:
        put(before)


def source_doc(doc_id: str, channel: str, received: dt.datetime, lines: list[str], *, sender: str | None = None,
               thread: str | None = None, subject: str | None = None) -> dict:
    """A source document in the corpus's shape (corpus/README.md), for ingest_all."""
    return {"id": doc_id, "kind": {"vendor_master": "vendor_master_record"}.get(channel, channel), "claimed_date": None,
            "header_date": received.isoformat() if channel == "email" else None, "scan": False,
            "custody": {"channel": channel, "parent_document_id": None, "received_at": received.isoformat(),
                        "sender": sender, "subject": subject, "thread_id": thread},
            "pages": [{"width": 612, "height": 792, "spans": [
                {"text": t, "x": 72, "y": 72 + 14 * i, "size": 10, "color": "#000000"} for i, t in enumerate(lines)]}]}
