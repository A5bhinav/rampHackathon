#!/usr/bin/env python3
"""Build the corpus scenarios and their frozen truth (spec 0014). Standard library only.

    uv run python corpus/generator/build.py            write corpus/{poison,controls,truth}/
    uv run python corpus/generator/build.py --check    rebuild in memory; fail on any difference

The truth's manifest digest is pinned below. If a change to the generator moves the truth,
`--check` fails until a person updates FROZEN_TRUTH_SHA256 in review, on purpose.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from scenarios import SCENARIOS, Scenario
from world import T0, Vendor, days, stamp

CORPUS = Path(__file__).resolve().parents[1]
OWNED = ("poison", "controls", "truth")
FROZEN_TRUTH_SHA256 = "91641fa25349f3aef30f2ff237d1447f2d6f4c902bc1e456b657efd835d1684f"


def dumps(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def run(s: Scenario) -> tuple[Vendor, dict]:
    v = Vendor(s.slug, s.vendor, s.area)
    expected = s.build(v)
    planted: dict[str, dict] = {}
    for sight in sorted(v.sightings, key=lambda x: (x["received_at"], x["document_id"], x["quote"])):
        entry = planted.setdefault(sight["e164"], {"e164": sight["e164"], "truth": sight["truth"], "sightings": []})
        assert entry["truth"] == sight["truth"], f"{s.id}: {sight['e164']} planted as both real and poison"
        entry["sightings"].append({k: sight[k] for k in sight if k not in ("e164", "truth")})
    truth = {
        "id": s.id, "kind": s.kind, "source": s.source, "title": s.title,
        "vendor": {"slug": s.slug, "name": v.name, "domain": v.domain, "lookalike": v.lookalike},
        "request_received_at": stamp(T0),
        "request_document_id": v.request_id,
        "ramp_change_at": stamp(T0 + days(2 / 24)),
        "expected": expected,
        "planted": [planted[k] for k in sorted(planted)],
    }
    return v, truth


def build() -> dict[str, str]:
    out: dict[str, str] = {}
    for s in SCENARIOS:
        v, truth = run(s)
        folder = f"{'controls' if s.kind == 'control' else 'poison'}/{s.id}-{s.slug}"
        truth["documents"] = f"{folder}/documents.json"
        out[f"{folder}/documents.json"] = dumps(v.docs)
        out[f"truth/{s.id}.json"] = dumps(truth)
    out["truth/manifest.sha256"] = "".join(f"{sha256(c)}  {Path(p).name}\n" for p, c in sorted(out.items())
                                           if p.startswith("truth/"))
    return out


def on_disk() -> dict[str, str]:
    return {str(p.relative_to(CORPUS)): p.read_text(encoding="utf-8")
            for top in OWNED if (CORPUS / top).is_dir() for p in sorted((CORPUS / top).rglob("*")) if p.is_file()}


def check() -> list[str]:
    want, have = build(), on_disk()
    errors = [f"missing   {p}" for p in sorted(want.keys() - have.keys())]
    errors += [f"stale     {p}" for p in sorted(have.keys() - want.keys())]
    errors += [f"differs   {p}" for p in sorted(want.keys() & have.keys()) if want[p] != have[p]]
    digest = sha256(want["truth/manifest.sha256"])
    if digest != FROZEN_TRUTH_SHA256:
        errors.append(f"truth moved: manifest digest {digest} is not the frozen {FROZEN_TRUTH_SHA256}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    if parser.parse_args().check:
        errors = check()
        print("\n".join(errors) if errors else f"corpus ok: {len(SCENARIOS)} scenarios, truth frozen at {FROZEN_TRUTH_SHA256}")
        return 1 if errors else 0
    for path, content in build().items():
        target = CORPUS / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    print(f"wrote {len(SCENARIOS)} scenarios; manifest digest {sha256(build()['truth/manifest.sha256'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
