"""Spec 0022 B: ingest() end to end on the frozen corpus, then select() reproduces the truth."""
import ast
import json
from collections import Counter

import pytest
from _world import CASE, DATASET, ROOT, VENDOR, at, corpus_world, run
from dialback_ledger.flags import ReaderMode, reader_mode
from dialback_ledger.ingest import ingest_all
from dialback_ledger.reader import ReaderUnavailable, StubReader, reader_for
from dialback_ledger.select import DocumentRef, Sighting
from test_extract import page, span

TRUTHS = sorted((ROOT / "corpus" / "truth").glob("*.json"))


def ingested(truth):
    return ingest_all(json.loads((ROOT / "corpus" / truth["documents"]).read_text()), extractor_version="test",
                      reader=StubReader())


def test_the_reader_flag_is_stub_unless_asked():
    assert reader_mode({}) is ReaderMode.STUB
    assert reader_mode({"DIALBACK_READER": "something"}) is ReaderMode.STUB
    assert reader_mode({"DIALBACK_READER": "llm"}) is ReaderMode.LLM
    with pytest.raises(ReaderUnavailable):
        reader_for(ReaderMode.LLM, env={})  # fails closed: without its key, llm mode has no reader (spec 0049)


def test_the_labeler_sees_only_visible_text():
    seen = []

    class Spy:
        def label(self, text):
            seen.append(text)
            return []

    doc = page(span("Main: 415-555-0117"), span("secret 415-555-0124", color="#ffffff"))
    result = ingest_all([doc], extractor_version="test", reader=Spy())
    assert seen == ["Main: 415-555-0117"]
    obs = sorted((o["value_normalized"], o["visibility"], o["role"], o["party"], o["quote"]) for o in result.observations)
    assert obs == [("+14155550117", "visible", "unknown", "unknown", "415-555-0117"),
                   ("+14155550124", "white", "unknown", "unknown", None)]


def test_provenance_comes_from_custody():
    result = ingest_all([page(span("Main: 415-555-0117"))], extractor_version="test", reader=StubReader())
    (o,) = result.observations
    assert (o["source_type"], o["seen_at"]) == ("email", at("2026-01-01T00:00:00Z"))


@pytest.mark.parametrize("path", TRUTHS, ids=lambda p: p.stem)
def test_gate_b_ingest_then_select_reproduces_the_truth(path):
    truth, inputs = corpus_world(path)
    gate_b(truth, inputs, ingested(truth))


@pytest.mark.parametrize("path", TRUTHS, ids=lambda p: p.stem)
def test_gate_b_through_pdfs(path):
    # Spec 0092, D8: every document rendered to a real PDF, then ingest_pdf (C04 rendered digital).
    from _pdfs import corpus_pdfs
    truth, inputs = corpus_world(path)
    docs = json.loads((ROOT / "corpus" / truth["documents"]).read_text())
    gate_b(truth, inputs, ingest_all(docs, extractor_version="test", reader=StubReader(), pdfs=corpus_pdfs(docs)))


def gate_b(truth, inputs, result):
    assert result.dropped == []
    # Everything select() reads comes from ingest(): its documents' custody and its observations.
    inputs["documents"] = [DocumentRef(d["id"], d["custody_channel"], d["received_at"], d["sender"], d["thread_id"],
                                       d["subject"], d["received_at"]) for d in result.documents]
    inputs["sightings"] = [Sighting(o["value_normalized"], o["document_id"], o["source_type"], o["seen_at"],
                                    o["visibility"], o["role"], o["party"], o["seen_at"])
                           for o in result.observations]
    out = run(inputs | {"case_id": CASE, "dataset_id": DATASET, "vendor_id": VENDOR})
    exp = truth["expected"]
    assert (at(out["cutoff"]), out["outcome"], out.get("e164")) == (at(exp["cutoff"]), exp["outcome"], exp["pick"])
    by = {c["e164"]: c for c in out["candidates"]}
    for e164, reasons in exp["rejected"].items():
        assert set(reasons) <= set(by[e164]["reasons"]), e164
    for e164 in exp.get("eligible", []):
        assert by[e164]["eligible"], (e164, by[e164]["reasons"])


def test_stub_reader_role_accuracy_is_reported():
    right, wrong = 0, []
    for path in TRUTHS:
        truth = json.loads(path.read_text())
        got = Counter((o["document_id"], o["value_normalized"], o["role"]) for o in ingested(truth).observations
                      if o["visibility"] == "visible")
        want = Counter((s["document_id"], p["e164"], s["role"]) for p in truth["planted"] for s in p["sightings"]
                       if s["visibility"] == "visible")
        right += sum((got & want).values())
        wrong += [(truth["id"], k) for k in (want - got)]
    accuracy = right / (right + len(wrong))
    print(f"stub reader role accuracy {accuracy:.3f}; mismatches: {wrong}")
    assert accuracy >= 0.95


def test_custody_must_be_the_documents_own():
    from dialback_ledger.custody import custody_for
    from dialback_ledger.ingest import ingest
    a, b = page(span("Main: 415-555-0117")), page(span("Main: 415-555-0117")) | {"id": "other"}
    with pytest.raises(ValueError):
        ingest(a, custody_for(b), extractor_version="test", reader=StubReader())


def test_an_attachment_cycle_is_refused():
    a = page() | {"id": "a"}
    b = page() | {"id": "b"}
    a["custody"] = a["custody"] | {"parent_document_id": "b"}
    b["custody"] = b["custody"] | {"parent_document_id": "a"}
    with pytest.raises(ValueError, match="cycle"):
        ingest_all([a, b], extractor_version="test", reader=StubReader())


def test_every_test_passes_a_reader_to_ingest():
    # Spec 0049, decision 8: the default reader comes from the environment, so a test that leaves it out
    # could call the model. Every test in the repo that ingests names its reader.
    missing = []
    for path in sorted(ROOT.glob("**/test_*.py")):
        if {".venv", "node_modules"} & set(path.parts):
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in {"ingest", "ingest_all", "ingest_pdf"}:
                reader = next((k.value for k in node.keywords if k.arg == "reader"), None)
                if reader is None or (isinstance(reader, ast.Constant) and reader.value is None):
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert missing == []


def test_the_stub_reader_says_vendor_only_for_a_contact_label():
    labels = StubReader().label("Accounts receivable: (415) 555-0142\nCall our bank at 415-555-0186\n"
                                "The verified callback number is 415-555-0169\nMain: 415-555-0117")
    assert [(lab["role"], lab["party"]) for lab in labels] == [("ar", "vendor"), ("unknown", "bank"), ("unknown", "unknown"),
                                                                 ("main", "vendor")]
