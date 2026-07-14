"""Cooperative cancellation for CPU-bound pattern generation."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class PatternGenerationCancelled(RuntimeError):
    """Raised at a generator checkpoint when its owning task was cancelled."""


_cancelled: ContextVar[Callable[[], bool] | None] = ContextVar(
    "pattern_generation_cancelled", default=None
)


@contextmanager
def cancellation_scope(check: Callable[[], bool] | None) -> Iterator[None]:
    token = _cancelled.set(check)
    try:
        yield
    finally:
        _cancelled.reset(token)


def cancellation_checkpoint() -> None:
    check = _cancelled.get()
    if check is not None and check():
        raise PatternGenerationCancelled("Pattern generation cancelled")
