"""Every lab test draws pinned (spec 0048, C1): a property test's sample depends on its seed alone, not
on which first-party modules the test session happened to import (FINDINGS B10)."""
from __future__ import annotations

import os
import shutil

import pytest
from dialback_evals.draws import pinned_draws


@pytest.fixture(autouse=True)
def _pinned_draws():
    with pinned_draws():
        yield


# Spec 0092, D10a: the legibility check reads pixels with Tesseract. CI always runs it for real. On a machine without
# Tesseract, the read is stood in for, and what needs a real read is skipped; CI never skips those.
HAS_TESSERACT = shutil.which("tesseract") is not None
REAL_OCR = HAS_TESSERACT or bool(os.environ.get("GITHUB_ACTIONS"))
_READ: dict[bytes, list] = {}


def pytest_collection_modifyitems(config, items):
    if not REAL_OCR:
        for item in items:
            if "test_pdf_tricks.py" in item.nodeid:
                item.add_marker(pytest.mark.skip(reason="needs Tesseract, which this machine lacks; CI runs it"))


@pytest.fixture(autouse=True)
def _ocr_for_tests(monkeypatch):
    from dialback_ledger import legibility
    if REAL_OCR:
        real = legibility._ocr

        def remembered(image, timeout):
            key = image.tobytes() + repr(image.shape).encode()
            if key not in _READ:
                _READ[key] = real(image, timeout)
            return _READ[key]

        monkeypatch.setattr(legibility, "_ocr", remembered)
    else:
        monkeypatch.setattr(legibility, "require_ocr", lambda: None)
        monkeypatch.setattr(legibility, "_read_matches", lambda image, expected, timeout: True)
