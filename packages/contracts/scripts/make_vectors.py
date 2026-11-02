#!/usr/bin/env python3
"""Golden hash vectors for the evidence chain, generated with the rfc8785 package.

    uv run --with rfc8785 python packages/contracts/scripts/make_vectors.py          # rewrite
    uv run --with rfc8785 python packages/contracts/scripts/make_vectors.py --check  # CI: fail on drift

The SQL side (audit.jcs, audit.content_hash, audit.chain_hash, audit.append_evidence) must
reproduce every value here; supabase/tests/005_hash_vectors.sql checks that. Python code
that builds envelopes (apps/core, packages/evidence) must use rfc8785.dumps() and the same
two hash steps, so a mismatch is caught here before it reaches the chain.
"""
import hashlib
import json
import sys
from pathlib import Path

import rfc8785

ROOT = Path(__file__).resolve().parents[1]
# A fixed list, not a glob: adding an example for a new event type must not change the golden
# vectors that SQL replays (and two branches adding examples must not fight over this file).
# To cover a new shape in the chain, add it here on purpose and regenerate.
VECTOR_EXAMPLES = [
    "evidence-event.change_detected.json",
    "evidence-event.decision_made.json",
    "evidence-event.release_approved.json",
]
EXAMPLES = [ROOT / "examples" / name for name in VECTOR_EXAMPLES]
OUT = ROOT / "vectors" / "evidence-chain.json"
GENESIS = "0" * 64


def jcs(obj) -> str:
    return rfc8785.dumps(obj).decode("utf-8")


def content_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def chain_hash(prev_hash: str, content: str) -> str:
    return hashlib.sha256((prev_hash + content).encode("utf-8")).hexdigest()


def build() -> dict:
    envelopes = [json.loads(p.read_text(encoding="utf-8")) for p in EXAMPLES]
    jcs_cases = [
        {
            "name": "keys sorted, escapes, nesting",
            "input": {"b": 1, "a": {"z": [True, None, 'x"y'], "y": "é", "t": "a\tb", "u": "\u0001/\u007f"}, "c": -5, "A": False},
        },
        {"name": "empty containers", "input": {"o": {}, "l": []}},
        {"name": "unicode above the BMP stays literal", "input": {"emoji": "🐍", "cjk": "漢字"}},
    ]
    for p, env in zip(EXAMPLES, envelopes):
        jcs_cases.append({"name": p.name, "input": env})
    for case in jcs_cases:
        case["jcs"] = jcs(case["input"])
        case["content_hash"] = content_hash(case["jcs"])

    hashes, prev = [], GENESIS
    for env in envelopes:
        h = chain_hash(prev, content_hash(jcs(env)))
        hashes.append(h)
        prev = h
    chain_ids = {env["chain_id"] for env in envelopes}
    assert len(chain_ids) == 1, "examples must share one chain_id"
    return {
        "generated_by": "packages/contracts/scripts/make_vectors.py (python rfc8785 + hashlib)",
        "rules": {
            "body_jcs": "RFC 8785 canonical JSON of the envelope",
            "content_hash": "hex(sha256(utf8(body_jcs)))",
            "hash": "hex(sha256(utf8(prev_hash || content_hash)))",
            "genesis_prev_hash": GENESIS,
        },
        "jcs": jcs_cases,
        "chain": {"chain_id": chain_ids.pop(), "events": envelopes, "hashes": hashes},
    }


def main() -> int:
    data = build()
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is out of date; run make_vectors.py", file=sys.stderr)
            return 1
        print(f"vectors up to date ({len(data['jcs'])} jcs cases, {len(data['chain']['hashes'])} chain events)")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
