#!/usr/bin/env python3
"""Validate every JSON Schema in schemas/ and every example in examples/ against its schema.

    uv run --with jsonschema --with rfc8785 python packages/contracts/scripts/validate.py

An example file is named <schema-stem>.<case>.json and validates against schemas/<schema-stem>.schema.json.
Numbers inside evidence envelopes must be integers (the chain refuses floats), which is checked here too.
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]


def integers_only(obj, path="$") -> list[str]:
    bad = []
    if isinstance(obj, float):
        bad.append(f"{path} is a float ({obj})")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            bad += integers_only(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += integers_only(v, f"{path}[{i}]")
    return bad


def main() -> int:
    schemas = {p: json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "schemas").glob("*.schema.json"))}
    registry = Registry()
    for p, s in schemas.items():
        Draft202012Validator.check_schema(s)
        res = Resource.from_contents(s)
        registry = registry.with_resource(s.get("$id", p.name), res).with_resource(f"./{p.name}", res)
        print(f"schema ok   {p.name}")

    failures = 0
    for ex in sorted((ROOT / "examples").glob("*.json")):
        stem = ex.name.split(".")[0]
        schema_path = ROOT / "schemas" / f"{stem}.schema.json"
        if schema_path not in schemas:
            print(f"no schema   {ex.name} (expected {schema_path.name})")
            failures += 1
            continue
        data = json.loads(ex.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schemas[schema_path], registry=registry)
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if stem == "evidence-event":
            errors_txt = [f"{'.'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors] + integers_only(data)
        else:
            errors_txt = [f"{'.'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]
        if errors_txt:
            failures += 1
            print(f"example BAD {ex.name}")
            for t in errors_txt:
                print(f"            {t}")
        else:
            print(f"example ok  {ex.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
