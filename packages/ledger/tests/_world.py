"""Builds select() inputs for the tests: hand-made worlds and the frozen corpus."""
import datetime as dt
import json
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
