#!/usr/bin/env python3
"""Validate every contract in packages/contracts.

    uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator \
        python packages/contracts/scripts/validate.py

1. Every JSON Schema in schemas/ is a valid 2020-12 schema.
2. Every example in examples/, named <schema-stem>.<case>.json, validates against
   schemas/<schema-stem>.schema.json. Evidence envelopes and selections must hold integers
   only (both are hashed, and the chain refuses floats).
3. Every OpenAPI document in openapi/ is a valid OpenAPI 3.1 document, and every inline
   request or response `example` validates against the schema beside it.

References between files resolve locally: relative paths from the referring file, and the
schemas' own `$id`s (https://dialback.dev/schemas/...) map to schemas/. Nothing is fetched.
"""
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
ID_PREFIX = "https://dialback.dev/schemas/"


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)


def retrieve(uri: str) -> Resource:
    """Load a referenced document from disk. Never touches the network."""
    if uri.startswith(ID_PREFIX):
        path = SCHEMAS / uri[len(ID_PREFIX):]
    else:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise LookupError(f"refusing to fetch {uri}: only local contracts resolve")
        path = Path(unquote(parsed.path))  # as_uri() escapes "+", spaces...; undo it
    return Resource.from_contents(load(path), default_specification=DRAFT202012)


REGISTRY = Registry(retrieve=retrieve)


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


def errors_for(schema: dict, instance) -> list[str]:
    validator = Draft202012Validator(schema, registry=REGISTRY)
    return [f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
            for e in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))]


def check_schemas_and_examples() -> int:
    failures = 0
    schemas = {p: load(p) for p in sorted(SCHEMAS.glob("*.schema.json"))}
    for p, s in schemas.items():
        Draft202012Validator.check_schema(s)
        print(f"schema ok    {p.name}")

    for ex in sorted((ROOT / "examples").glob("*.json")):
        stem = ex.name.split(".")[0]
        schema_path = SCHEMAS / f"{stem}.schema.json"
        if schema_path not in schemas:
            print(f"no schema    {ex.name} (expected {schema_path.name})")
            failures += 1
            continue
        data = load(ex)
        errors = errors_for(schemas[schema_path], data)
        if stem in ("evidence-event", "selection"):  # both are hashed; floats are refused
            errors += integers_only(data)
        if errors:
            failures += 1
            print(f"example BAD  {ex.name}")
            for t in errors:
                print(f"             {t}")
        else:
            print(f"example ok   {ex.name}")
    return failures


def pointer(*parts: str) -> str:
    """A JSON pointer from raw keys (RFC 6901 escaping)."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def follow(doc: dict, node, ptr: str):
    """Follow same-document $refs (components/responses and the like), tracking the pointer."""
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/"):
        ptr = node["$ref"][1:]
        node = doc
        for part in ptr[1:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
    return node, ptr


def inline_examples(doc: dict):
    """Yield (where, schema_pointer, example) for every media-type example in the paths."""
    for path, item in (doc.get("paths") or {}).items():
        for method, op in item.items():
            if not isinstance(op, dict) or method.startswith("x-") or method in ("servers", "parameters"):
                continue
            body, body_ptr = follow(doc, op.get("requestBody"), pointer("paths", path, method, "requestBody"))
            for media, content in ((body or {}).get("content") or {}).items():
                if "example" in content and "schema" in content:
                    yield f"{method.upper()} {path} request", body_ptr + pointer("content", media, "schema"), content["example"]
            for status, resp in (op.get("responses") or {}).items():
                resp, resp_ptr = follow(doc, resp, pointer("paths", path, method, "responses", status))
                for media, content in ((resp or {}).get("content") or {}).items():
                    if "example" in content and "schema" in content:
                        yield f"{method.upper()} {path} {status}", resp_ptr + pointer("content", media, "schema"), content["example"]


def check_openapi() -> int:
    from openapi_spec_validator import validate as validate_spec

    failures = 0
    for doc_path in sorted((ROOT / "openapi").glob("*.yaml")):
        doc = load(doc_path)
        uri = doc_path.as_uri()
        try:
            validate_spec(doc, base_uri=uri)
        except Exception as exc:  # noqa: BLE001  (the validator raises several unrelated error types)
            failures += 1
            print(f"openapi BAD  {doc_path.name}: {str(exc).splitlines()[0]}")
            continue
        print(f"openapi ok   {doc_path.name}")
        for where, schema_ptr, example in inline_examples(doc):
            # Point at the schema's own location in the document, so #/components and
            # ../schemas/... references resolve exactly as an OpenAPI tool would.
            errors = errors_for({"$ref": f"{uri}#{schema_ptr}"}, example)
            if errors:
                failures += 1
                print(f"example BAD  {doc_path.name} {where}")
                for t in errors:
                    print(f"             {t}")
            else:
                print(f"example ok   {doc_path.name} {where}")
    return failures


def main() -> int:
    failures = check_schemas_and_examples()
    failures += check_openapi()
    if failures:
        print(f"{failures} contract problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
