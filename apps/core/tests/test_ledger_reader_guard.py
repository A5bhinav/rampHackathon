"""Core tests never call the LLM reader's API by accident (spec 0049, decision 8).

`dialback-core demo prepare` loads the Trust Ledger with the reader DIALBACK_READER names, so a
shell with `DIALBACK_READER=llm` and a key would send the seed's documents to the model. The
conftest pins the stub for every core test."""
from __future__ import annotations

from dialback_ledger.flags import ReaderMode, reader_mode


def test_core_tests_read_documents_with_the_stub():
    assert reader_mode() is ReaderMode.STUB
