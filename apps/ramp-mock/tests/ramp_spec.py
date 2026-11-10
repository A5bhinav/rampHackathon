"""Check a Ramp-shaped route against Ramp's published Developer API document (spec 0078).

`operation_problems` checks what a route accepts, `response_problems` what it answers, and
`payload_problems` any JSON body (a webhook, an example) against one of Ramp's schemas by name. Each
returns every problem as a sentence, and an empty list when there is none. Anything the checker can't
compare is a problem, never a skip (fail closed).
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cache
from pathlib import Path

from openapi_schema_validator import OAS30ReadValidator, oas30_format_checker

DOC_PATH = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi" / "ramp-developer-api.json"
METHODS = ("get", "put", "post", "patch", "delete")
COMPOSITIONS = ("allOf", "anyOf", "oneOf")
SCHEMA_REF = "#/components/schemas/"


@cache
def _doc() -> dict:
    return json.loads(DOC_PATH.read_text(encoding="utf-8"))


def _resolve(node: dict, doc: dict) -> dict:
    """Follow local `$ref`s (`#/components/...`) until a node isn't one."""
    while isinstance(node, dict) and "$ref" in node:
        target = doc
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _properties(schema: dict, doc: dict) -> set[str]:
    """Every property name a schema allows, through `$ref`s and `allOf`/`anyOf`/`oneOf`."""
    schema = _resolve(schema, doc)
    names = set(schema.get("properties", {}))
    for key in COMPOSITIONS:
        for branch in schema.get(key, []):
            names |= _properties(branch, doc)
    return names


def _strict(schema):
    """D1.4: a field Ramp doesn't define is a problem; a field Ramp requires may be left out."""
    if isinstance(schema, list):
        return [_strict(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "required" and isinstance(value, list):
            continue
        if key == "properties":
            out[key] = {name: _strict(sub) for name, sub in value.items()}
        elif key in ("items", "additionalProperties", "not", *COMPOSITIONS):
            out[key] = _strict(value)
        else:
            out[key] = value
    if "properties" in out and "additionalProperties" not in out:
        out["additionalProperties"] = False
    return out


@cache
def _strict_components() -> dict:
    return {"schemas": {name: _strict(s) for name, s in _doc()["components"]["schemas"].items()}}


def _operation(method: str, path: str) -> tuple[dict | None, str | None]:
    item = _doc()["paths"].get(path)
    if item is None:
        return None, f"{path} is not in Ramp's document"
    op = item.get(method.lower())
    if op is None:
        ramp_has = ", ".join(m.upper() for m in METHODS if m in item)
        return None, f"{method.upper()} {path} is not in Ramp's document (Ramp has {ramp_has})"
    return op, None


def operation_problems(method: str, path: str, *, query: set[str], body: set[str]) -> list[str]:
    """D1.1 and D1.2: Ramp has this path and method, and documents every input the mock accepts."""
    op, missing = _operation(method, path)
    if missing:
        return [missing]
    doc, where = _doc(), f"{method.upper()} {path}"
    declared = [_resolve(p, doc) for p in doc["paths"][path].get("parameters", []) + op.get("parameters", [])]
    ramp_query = {p["name"] for p in declared if p.get("in") == "query"}
    request = _resolve(op.get("requestBody", {}), doc).get("content", {}).get("application/json", {})
    ramp_body = _properties(request["schema"], doc) if "schema" in request else set()
    problems = [f"{where}: query parameter {name!r} isn't one Ramp documents ({', '.join(sorted(ramp_query)) or 'none'})"
                for name in sorted(query - ramp_query)]
    problems += [f"{where}: body field {name!r} isn't one Ramp documents ({', '.join(sorted(ramp_body)) or 'no body'})"
                 for name in sorted(body - ramp_body)]
    return problems


def _content(op: dict, code: str) -> dict:
    return _resolve(op["responses"][code], _doc()).get("content", {})


def response_problems(method: str, path: str, status: int, content: bytes) -> list[str]:
    """D1.3 and D1.4: Ramp documents this status, and the body fits Ramp's schema for it."""
    op, missing = _operation(method, path)
    if missing:
        return [missing]
    where = f"{method.upper()} {path} {status}"
    documented = sorted(code for code in op["responses"] if code.startswith("2"))
    if str(status) not in documented:
        problems = [f"{where}: Ramp answers this with {', '.join(documented)}, not {status}"]
        bodies = [(code, _content(op, code)["application/json"]["schema"]) for code in documented
                  if code != "204" and "schema" in _content(op, code).get("application/json", {})]
        if len(bodies) == 1:  # D1.3: still compare the body with Ramp's one success body
            code, schema = bodies[0]
            problems += _body_problems(f"{where} (against Ramp's {code} body)", schema, content)
        return problems
    if status == 204:  # a 204 never has a body, whatever Ramp's document says
        return [] if not content else [f"{where}: a 204 has no body, but the mock sent {len(content)} bytes"]
    types = list(_content(op, str(status)))
    if types and "application/json" not in types:
        return [f"{where}: Ramp sends {', '.join(types)} here, which this checker can't compare"]
    schema = _content(op, str(status)).get("application/json", {}).get("schema")
    if schema is None:
        return [] if not content else [f"{where}: Ramp sends no body here, but the mock sent {len(content)} bytes"]
    return _body_problems(where, schema, content)


def payload_problems(schema_name: str, content: bytes) -> list[str]:
    """A JSON body against one of Ramp's schemas, by name, under the same rules as a response."""
    if schema_name not in _doc()["components"]["schemas"]:
        return [f"{schema_name} is not a schema in Ramp's document"]
    return _body_problems(schema_name, {"$ref": SCHEMA_REF + schema_name}, content)


def _body_problems(where: str, schema: dict, content: bytes) -> list[str]:
    try:
        body = json.loads(content)
    except ValueError:
        return [f"{where}: Ramp sends JSON here, but the mock's body isn't JSON"]
    if body is None:
        return [f"{where}: the body is null"]
    validator = OAS30ReadValidator({"components": _strict_components(), "allOf": [_strict(schema)]},
                                   format_checker=oas30_format_checker)
    # D1.5: a null value is never a problem, because Ramp's own examples put null in fields its schema
    # calls non-nullable. An unknown field is still named, null or not: that error is about its object.
    errors = sorted((e for e in validator.iter_errors(body) if e.instance is not None),
                    key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{where}: {'.'.join(map(str, e.absolute_path)) or '(body)'}: {_short(e.message)}" for e in errors]


def _short(message: str, limit: int = 240) -> str:
    """An enum of 589 audit event types is one message; keep each readable."""
    return message if len(message) <= limit else message[:limit] + " ..."


def ramp_examples(operations: Iterable[tuple[str, str]]) -> list[tuple[str, dict]]:
    """(schema name, example) for each schema these operations' success responses reach that carries an
    object example, so Ramp's own data can be run through the checker."""
    doc, seen = _doc(), set()

    def walk(node) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref is not None:
                if ref not in seen:
                    seen.add(ref)
                    walk(_resolve(node, doc))
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for method, path in operations:
        for code, response in doc["paths"][path][method]["responses"].items():
            if code.startswith("2"):
                walk(response)
    schemas = doc["components"]["schemas"]
    names = sorted(ref.removeprefix(SCHEMA_REF) for ref in seen if ref.startswith(SCHEMA_REF))
    return [(name, schemas[name]["example"]) for name in names if isinstance(schemas[name].get("example"), dict)]


def mock_inputs(mock_doc: dict, method: str, path: str) -> tuple[set[str], set[str]]:
    """The query parameters and JSON body fields a route accepts, read from the mock's own OpenAPI."""
    op = mock_doc["paths"][path][method.lower()]
    query = {p["name"] for p in op.get("parameters", []) if p.get("in") == "query"}
    request = op.get("requestBody", {}).get("content", {}).get("application/json", {})
    return query, _properties(request["schema"], mock_doc) if "schema" in request else set()
