"""Pinned draws for every lab sample (spec 0048, C1; FINDINGS B10).

Hypothesis 6.170 draws, about one integer in twenty, a literal it mined from every first-party module
loaded at that moment (our workspace packages are installed editable, so they all count). So one seed
drew different worlds in a bare script, in the CLI, in a bench worker and under pytest, depending on
what each had imported. `pinned_draws()` empties that mined pool, so a seed alone decides the draws;
Hypothesis's own built-in constants still apply. This is spec 0046's `guided.pinned_draws()`, moved
here so every lab sample shares it: `lab fuzz --search first`, `lab reports`, the property tests (an
autouse fixture in `evals/tests/conftest.py`) and, once the lab lands, spec 0046's searches.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator

from hypothesis import HealthCheck, given, settings
from hypothesis import seed as pin_seed
from hypothesis.internal.conjecture import providers as _providers
from hypothesis.internal.constants_ast import Constants as _Constants


@contextlib.contextmanager
def pinned_draws() -> Iterator[None]:
    """Draw from the seed alone: inside this block the pool of literals mined from loaded modules is
    empty. Safe to nest. Raises if this Hypothesis no longer has the hook it replaces, so an upgrade
    that moves it fails loudly instead of drifting again."""
    get, cache = getattr(_providers, "_get_local_constants", None), getattr(_providers, "CONSTANTS_CACHE", None)
    if get is None or cache is None:
        raise RuntimeError("this Hypothesis has no _get_local_constants: re-check pinned_draws against it")
    _providers._get_local_constants = _Constants
    cache.cache.clear()
    try:
        yield
    finally:
        _providers._get_local_constants = get
        cache.cache.clear()


def run_pinned(strategy, body: Callable[[object], None], *, examples: int, seed: int) -> None:
    """Call `body` on each value Hypothesis draws from `strategy` at `seed`, with a budget of `examples`
    (Hypothesis's `max_examples`), under pinned draws. An exception from `body` stops the run and is
    shrunk by Hypothesis, as `lab fuzz --search first` wants."""
    @pin_seed(seed)
    @settings(max_examples=examples, deadline=None, database=None, report_multiple_bugs=False,
              suppress_health_check=[HealthCheck.too_slow])
    @given(strategy)
    def each(value) -> None:
        body(value)

    with pinned_draws():
        each()


def sample(strategy, *, examples: int, seed: int) -> list:
    """The values drawn from `strategy` at `seed` with a budget of `examples`, in the order drawn
    (a value Hypothesis draws twice appears twice)."""
    drawn: list = []
    run_pinned(strategy, drawn.append, examples=examples, seed=seed)
    return drawn


def world_digests(*, examples: int, seed: int) -> list[str]:
    """The content digests of the worlds `worldgen.worlds()` draws at `seed`: what C1 compares across processes."""
    from .gencorpus import world_digest
    from .worldgen import worlds
    return [world_digest(w) for w in sample(worlds(), examples=examples, seed=seed)]
