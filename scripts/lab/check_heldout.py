"""Check a sealed held-out scenario file and print its hash (spec 0008, D5).

The held-out scenarios never enter the repo. This validates the file where it lives (a CI
secret or private bucket) against the scenario format, checks the rules the design sets for
the sealed set, and prints the SHA-256 that is committed instead of the file.

    uv run --with jsonschema python scripts/lab/check_heldout.py heldout.jsonl \\
        [--schema packages/contracts/schemas/scenario.schema.json]

Rules: one dialback.scenario/v1 family per line; held_out true; no `expected` block (the
oracle writes it); fictional numbers only (+1 NPA 555-0100 to 555-0199); unique family ids.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
FICTIONAL = re.compile(r"^\+1[2-9][0-9]{2}55501[0-9]{2}$")


def local_schemas(schema_path: Path) -> Registry:
    """Resolve $refs (the scenario format refers to evidence-event's Outcome) from the schema's own
    folder and the repo's contracts. Never the network: a sealed file is checked offline."""
    resources = []
    for folder in {schema_path.resolve().parent, ROOT / "packages" / "contracts" / "schemas"}:
        for path in folder.glob("*.schema.json"):
            doc = json.loads(path.read_text())
            resources.append((doc.get("$id", path.as_uri()), Resource.from_contents(doc, default_specification=DRAFT202012)))

    def refuse(uri: str):
        raise LookupError(f"{uri} is not a local schema")
    return Registry(retrieve=refuse).with_resources(resources)


def problems(families: list[dict], schema: dict, registry: Registry | None = None) -> list[str]:
    validator = Draft202012Validator(schema, registry=registry or Registry())
    found, seen = [], set()
    for i, fam in enumerate(families, 1):
        where = f"line {i} ({fam.get('family_id', '?')})"
        found += [f"{where}: {e.message}" for e in validator.iter_errors(fam)]
        if fam.get("held_out") is not True:
            found.append(f"{where}: held_out must be true")
        if fam.get("family_id") in seen:
            found.append(f"{where}: duplicate family_id")
        seen.add(fam.get("family_id"))
        for v in fam.get("variants", []):
            if "expected" in v:
                found.append(f"{where}: variant {v.get('variant_id')} has an expected block; the oracle writes it")
        numbers = [p.get("e164", "") for p in fam.get("facts", {}).get("phone_numbers", [])]
        numbers += [p.get("e164", "") for v in fam.get("variants", []) for p in (v.get("overrides") or {}).get("phone_numbers", [])]
        found += [f"{where}: {e} is not a fictional 555-01xx number" for e in numbers if not FICTIONAL.match(e)]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--schema", type=Path, default=ROOT / "packages" / "contracts" / "schemas" / "scenario.schema.json")
    args = parser.parse_args()
    raw = args.file.read_bytes()
    families = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    found = problems(families, json.loads(args.schema.read_text()), local_schemas(args.schema))
    for p in found:
        print(f"problem  {p}", file=sys.stderr)
    variants = sum(len(f.get("variants", [])) for f in families)
    print(f"{len(families)} families, {variants} variants, {len(found)} problems")
    print(f"sha256   {hashlib.sha256(raw).hexdigest()}  {args.file.name}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
