"""Every ledger test runs without a model key and without a network for the SDK (spec 0049, decision 8).

So no test can call the Anthropic API by accident: the key and the reader flag are removed from the
environment, and the SDK's real HTTP transport refuses to send. Tests that exercise the LLM reader
fake the API with `httpx2.MockTransport`, under the real SDK.
"""
import contextlib
import json
import os
import shutil
from pathlib import Path

import httpx2
import pytest

_UNSET = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL", "DIALBACK_READER")


@contextlib.contextmanager
def no_network(monkeypatch):
    """Block the SDK's real HTTP transport. Each blocked call is recorded, and the test fails when the
    guard closes, even if the code under test caught the error (the SDK retries it and the reader
    turns it into ReaderFailed, so a test expecting a failure would otherwise pass; review G19)."""
    blocked: list[str] = []

    def refuse(self, request):
        blocked.append(request.url.host)
        raise RuntimeError(f"a test tried to reach {request.url.host} over the network")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)
    yield blocked
    if blocked:
        pytest.fail(f"a test reached for the network {len(blocked)} times ({', '.join(sorted(set(blocked)))})",
                    pytrace=False)


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    for name in _UNSET:
        monkeypatch.delenv(name, raising=False)
    with no_network(monkeypatch):
        yield


# Spec 0092, D10a: the legibility check reads pixels with Tesseract. CI always runs it for real. On a machine
# without Tesseract, the read is stood in for and only what needs a real read is skipped; CI never skips those.
HAS_TESSERACT = shutil.which("tesseract") is not None
REAL_OCR = HAS_TESSERACT or bool(os.environ.get("GITHUB_ACTIONS"))
NEEDS_REAL_OCR = ("overstruck_by_slightly_rotated_letters", "overstruck_by_paths", "gray_digits_on_black_and_white_noise",
                  "digits_overlapped_by_narrow_widths", "digits_squashed_behind_wide_widths")
KNOWN = json.loads((Path(__file__).resolve().parents[3] / "evals" / "known-failures.json").read_text(encoding="utf-8"))
ANOTHER_VIEWER = {e["case"]: e["finding"] for e in KNOWN.get("lab-pdf", []) if "covered_only_in_another_viewer" in e["labels"]}


def pytest_configure(config):
    config.addinivalue_line("markers", "real_ocr: reads with Tesseract itself; skipped only off CI, without Tesseract")


def pytest_collection_modifyitems(config, items):
    for item in items:
        case = item.callspec.id if hasattr(item, "callspec") else None
        if item.originalname == "test_a_number_another_viewer_covers_is_never_eligible" and case in ANOTHER_VIEWER:
            # Spec 0092, D10a: PDFium is the only renderer judged. Strict: it goes red if one starts passing.
            item.add_marker(pytest.mark.xfail(strict=True, reason=f"known failure, finding {ANOTHER_VIEWER[case]}"))
        if not REAL_OCR and (item.get_closest_marker("real_ocr") or case in NEEDS_REAL_OCR):
            item.add_marker(pytest.mark.skip(reason="needs Tesseract, which this machine lacks; CI runs it"))


@pytest.fixture(autouse=True)
def _ocr_for_tests(monkeypatch):
    from dialback_ledger import legibility
    if REAL_OCR:
        monkeypatch.setattr(legibility, "_ocr", _remembered(legibility._ocr))
    else:
        monkeypatch.setattr(legibility, "require_ocr", lambda: None)
        monkeypatch.setattr(legibility, "_read_matches", lambda image, expected, timeout: True)


_READ: dict[bytes, list] = {}


def _remembered(ocr):
    """Tesseract once per distinct image: the gates read the same documents several times."""
    def remembered(image, timeout):
        key = image.tobytes() + repr(image.shape).encode()
        if key not in _READ:
            _READ[key] = ocr(image, timeout)
        return _READ[key]
    return remembered
