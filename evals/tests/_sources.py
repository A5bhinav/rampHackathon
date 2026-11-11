"""The page models spec 0092 renders to PDF and reads back (D7): the corpus, the demo seed, and spec 0052's
generated documents (every frozen case, and seeds 0 to 4 at 200 documents each, with each seed's worlds)."""
from __future__ import annotations

import dataclasses
import functools
import json
import os
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCGEN_SEEDS = range(5)
DOCGEN_EXAMPLES = 200


def corpus_documents() -> Iterator[tuple[str, dict]]:
    for path in sorted((ROOT / "corpus").glob("*/*/documents.json")):
        scenario = path.parent.name.split("-")[0]
        for doc in json.loads(path.read_text(encoding="utf-8")):
            yield f"corpus-{scenario}-{doc['id'][:8]}", doc


def demo_documents() -> Iterator[tuple[str, dict]]:
    from dialback_ledger.demo import load_seed
    for ref, vendor in load_seed()["vendors"].items():
        for doc in [*vendor["documents"], vendor["request"]]:
            yield f"demo-{ref}-{doc['id'][:8]}", doc


@functools.cache
def _docgen() -> tuple[tuple[str, dict], ...]:
    from dialback_evals import docgen
    out = []
    for case_dir in sorted(p for p in (ROOT / "evals" / "docs-generated").iterdir() if (p / docgen.FILE).is_file()):
        _, documents, _ = docgen.load_case(case_dir)
        out += [(f"docs-frozen-{case_dir.name}-{k}", d) for k, d in enumerate(documents)]
    for seed in DOCGEN_SEEDS:
        for plan in docgen.draw_docs(examples=DOCGEN_EXAMPLES, seed=seed):
            out.append((f"docs-seed{seed}-{plan.id}", docgen.render(plan)))
        for world in docgen.draw_worlds(seed=seed):
            out += [(f"docs-seed{seed}-{world.id}-{d['id']}", d) for d in docgen.rendered(world)]
    return tuple(out)


def docgen_documents() -> Iterator[tuple[str, dict]]:
    yield from _docgen()


def all_documents() -> list[tuple[str, dict]]:
    return [*corpus_documents(), *demo_documents(), *docgen_documents()]


def round_trip_documents() -> list[tuple[str, dict]]:
    """What the round trip reads. Spec 0092, D10a puts Tesseract on every visible number, so CI runs the corpus, the
    demo seed and the frozen cases; PDF_ROUND_TRIP_FULL=1 runs everything (the full set runs once, per the spec)."""
    if os.environ.get("PDF_ROUND_TRIP_FULL"):
        return all_documents()
    return [*corpus_documents(), *demo_documents(), *(s for s in docgen_documents() if s[0].startswith("docs-frozen-"))]


def bank_strings() -> list[str]:
    """Every string in spec 0052's phrase and layout banks (docbank), however deeply nested."""
    from dialback_evals import docbank

    def walk(v) -> Iterator[str]:
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for k, x in v.items():
                yield from walk(k)
                yield from walk(x)
        elif isinstance(v, (list, tuple, set, frozenset)):
            for x in v:
                yield from walk(x)
        elif dataclasses.is_dataclass(v) and not isinstance(v, type):
            for f in dataclasses.fields(v):
                yield from walk(getattr(v, f.name))

    return [s for name, v in vars(docbank).items() if name.isupper() for s in walk(v)]
