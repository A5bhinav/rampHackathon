"""Spec 0079's gate: every demo vendor keeps its pick. The demo seed goes through ingest_all, as
`dialback-ledger demo load` and `demo request` put it in the ledger, and both rules run on it in
memory (no database). The stories are test_demo.py's: Acme and Cobalt call their AR line,
Brightline gets no call."""
import datetime as dt
import uuid

import pytest
from _world import calls, frozen, run
from dialback_ledger.demo import load_seed
from dialback_ledger.ingest import ingest_all
from dialback_ledger.reader import StubReader
from dialback_ledger.select import ChangeRequest, DocumentRef, Sighting

SEED = load_seed()
ANCHOR = dt.datetime.fromisoformat(SEED["anchor"]).astimezone(dt.UTC)
STORIES = {"acme": SEED["vendors"]["acme"]["phones"]["ar"], "brightline": None, "cobalt": SEED["vendors"]["cobalt"]["phones"]["ar"]}


def demo_world(ref: str) -> dict:
    """The vendor's history and its change request, ingested at the seed's own dates; the case opens
    ten minutes after the request arrives."""
    v = SEED["vendors"][ref]
    result = ingest_all([*v["documents"], v["request"]], extractor_version="test", reader=StubReader())

    def when(t: str | dt.datetime) -> dt.datetime:
        return t if isinstance(t, dt.datetime) else dt.datetime.fromisoformat(t).astimezone(dt.UTC)

    recorded = ANCHOR
    docs = [DocumentRef(d["id"], d["custody_channel"], when(d["received_at"]), d["sender"], d["thread_id"], d["subject"],
                        recorded) for d in result.documents]
    received = {d.id: d.received_at for d in docs}
    sightings = [Sighting(o["value_normalized"], o["document_id"], o["source_type"], received[o["document_id"]],
                          o["visibility"], o["role"], o["party"], recorded) for o in result.observations if o["kind"] == "phone"]
    req = next(d for d in docs if d.id == v["request"]["id"])
    return {"case_id": uuid.UUID(int=1), "dataset_id": uuid.UUID(int=2), "vendor_id": uuid.UUID(int=3),
            "vendor_domains": (v["domain"],), "request": ChangeRequest(req.id, req.received_at, req.thread_id, req.subject, req.sender),
            "requested_cutoff": req.received_at + dt.timedelta(minutes=5), "case_opened_at": req.received_at + dt.timedelta(minutes=10),
            "documents": docs, "sightings": sightings}


def test_the_seed_has_three_vendors():
    assert set(SEED["vendors"]) == set(STORIES)


@pytest.mark.parametrize("ref", sorted(STORIES))
def test_every_demo_vendor_keeps_its_pick(ref, monkeypatch):
    monkeypatch.delenv("DIALBACK_READER", raising=False)     # the stub reader, as the demo loads it by default
    monkeypatch.delenv("DIALBACK_CONTEXT_GUARD", raising=False)
    w = demo_world(ref)
    today, new = frozen(w), run(w)
    assert today.get("e164") == STORIES[ref], "the demo story, under today's rule"
    assert (new["outcome"], new.get("e164")) == (today["outcome"], today.get("e164"))
    assert new["candidates"] == today["candidates"]
    assert calls(new) == calls(today)[:len(calls(new))]
