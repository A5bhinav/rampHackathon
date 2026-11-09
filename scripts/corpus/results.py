#!/usr/bin/env python3
"""Run the real number-picking rule on the 20 frozen corpus cases and write what it did (spec 0038).

    uv run python scripts/corpus/results.py           # rewrite apps/web/src/data/corpus-results.json
    uv run python scripts/corpus/results.py --check   # exit 1 if the committed file is stale

The inputs are built exactly as packages/ledger/tests/test_select_corpus.py builds them, and
`matches` applies the same checks that gate does. The dashboard only shows this file; it
computes nothing. This is the selection rule against planted attacks, not the attack lab.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from pathlib import Path

from dialback_ledger.select import ChangeRequest, DocumentRef, Sighting, select

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "apps" / "web" / "src" / "data" / "corpus-results.json"
CASE, DATASET, VENDOR = (uuid.UUID(int=n) for n in (1, 2, 3))


def at(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def inputs(truth: dict) -> dict:
    raw = json.loads((ROOT / "corpus" / truth["documents"]).read_text())
    docs = [DocumentRef(d["id"], d["custody"]["channel"], at(d["custody"]["received_at"]), d["custody"]["sender"],
                        d["custody"]["thread_id"], d["custody"]["subject"], at(d["custody"]["received_at"])) for d in raw]
    sightings = [Sighting(p["e164"], s["document_id"], s["channel"], at(s["received_at"]), s["visibility"], s["role"],
                          s["party"], at(s["received_at"])) for p in truth["planted"] for s in p["sightings"]]
    req = next(d for d in docs if d.id == truth["request_document_id"])
    ramp = at(truth["ramp_change_at"])
    return {"case_id": CASE, "dataset_id": DATASET, "vendor_id": VENDOR, "vendor_domains": (truth["vendor"]["domain"],),
            "request": ChangeRequest(req.id, req.received_at, req.thread_id, req.subject, req.sender),
            "requested_cutoff": ramp, "case_opened_at": ramp + dt.timedelta(hours=1), "documents": docs, "sightings": sightings}


def problems(truth: dict, out: dict) -> list[str]:
    """What test_select_corpus.py would fail on, in words; empty when the rule did what the truth says."""
    exp, found = truth["expected"], []
    by_number = {c["e164"]: c for c in out["candidates"]}
    if at(out["cutoff"]) != at(exp["cutoff"]):
        found.append("cutoff differs")
    if out["outcome"] != exp["outcome"] or out.get("e164") != exp["pick"]:
        found.append("a different pick")
    for e164, reasons in exp["rejected"].items():
        c = by_number.get(e164)
        if c is None or c["eligible"] or not set(reasons) <= set(c["reasons"]):
            found.append(f"{e164} not refused for the expected reasons")
    for e164 in exp.get("eligible", []):
        if not by_number.get(e164, {}).get("eligible"):
            found.append(f"{e164} should stay eligible")
    poison = {p["e164"] for p in truth["planted"] if p["truth"] == "poison"}
    if truth["kind"] != "known_limit" and out.get("e164") in poison:
        found.append("a poisoned number was picked")
    return found


def result(path: Path) -> dict:
    truth = json.loads(path.read_text())
    w = inputs(truth)
    out = select(**w)
    by_number = {c["e164"]: c for c in out["candidates"]}
    planted = []
    for p in truth["planted"]:
        seen = sorted(s["received_at"] for s in p["sightings"])
        c = by_number.get(p["e164"], {})
        planted.append({"e164": p["e164"], "truth": p["truth"], "sightings": len(p["sightings"]),
                        "channels": sorted({s["channel"] for s in p["sightings"]}),
                        "hidden": any(s["visibility"] != "visible" for s in p["sightings"]),
                        "first_seen_at": seen[0] if seen else None,
                        "eligible": c.get("eligible", False), "reasons": c.get("reasons", [])})
    found = problems(truth, out)
    return {"id": truth["id"], "kind": truth["kind"], "title": truth["title"], "vendor": truth["vendor"]["name"],
            "request": {"subject": w["request"].subject, "received_at": truth["request_received_at"]},
            "expected": {"outcome": truth["expected"]["outcome"], "pick": truth["expected"]["pick"]},
            "actual": {"outcome": out["outcome"], "pick": out.get("e164"), "reason": out.get("reason")},
            "matches": not found, "problems": found,
            "picked_planted": any(p["truth"] == "poison" and p["e164"] == out.get("e164") for p in truth["planted"]),
            "planted": planted}


def build() -> dict:
    truths = sorted((ROOT / "corpus" / "truth").glob("*.json"))
    cases = [result(p) for p in truths]
    from dialback_ledger.reasons import REASON_TEXT
    from dialback_ledger.select import RULE_VERSION
    return {"generated_by": "scripts/corpus/results.py", "rule_version": RULE_VERSION, "reason_text": REASON_TEXT,
            "cases": cases}


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    text = render(build())
    if ap.parse_args().check:
        ok = OUT.exists() and OUT.read_text() == text
        print("corpus results current" if ok else f"{OUT.relative_to(ROOT)} is stale: run scripts/corpus/results.py")
        return 0 if ok else 1
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
