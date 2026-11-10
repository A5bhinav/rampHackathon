"""Every lab test draws pinned (spec 0048, C1): a property test's sample depends on its seed alone, not
on which first-party modules the test session happened to import (FINDINGS B10)."""
from __future__ import annotations

import pytest
from dialback_evals.draws import pinned_draws


@pytest.fixture(autouse=True)
def _pinned_draws():
    with pinned_draws():
        yield
