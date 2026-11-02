"""Check responses against packages/contracts/openapi/dialback.yaml itself, so the core and
the dashboard can't drift from the contract they share."""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts"
DOC = CONTRACTS / "openapi" / "dialback.yaml"
ID_PREFIX = "https://dialback.dev/schemas/"


def _load(path: Path):
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) if path.suffix == ".yaml" else json.loads(text)


def _retrieve(uri: str) -> Resource:
    path = CONTRACTS / "schemas" / uri[len(ID_PREFIX):] if uri.startswith(ID_PREFIX) else Path(urlparse(uri).path)
    return Resource.from_contents(_load(path), default_specification=DRAFT202012)


@cache
def _doc() -> dict:
    return _load(DOC)


def _escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def assert_matches(path: str, method: str, status: int, body) -> None:
    """Validate a response body against the operation's schema for that status."""
    doc = _doc()
    response = doc["paths"][path][method]["responses"][str(status)]
    pointer = "/" + "/".join(_escape(p) for p in ("paths", path, method, "responses", str(status)))
    if "$ref" in response:
        pointer = response["$ref"][1:]
    pointer += "/content/application~1json/schema"
    validator = Draft202012Validator({"$ref": f"{DOC.as_uri()}#{pointer}"}, registry=Registry(retrieve=_retrieve))
    errors = sorted(validator.iter_errors(body), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in errors[:10])
