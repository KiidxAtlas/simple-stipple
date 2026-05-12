"""Qt lifecycle helpers — exception-safe signal blocking, weak-bound callbacks."""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from typing import Any

from PySide6.QtCore import QObject

_LOG = logging.getLogger(__name__)


@contextmanager
def block_signals(*widgets: QObject):
    """Temporarily block signals on one or more widgets, restoring on exit.

    Restores prior state even when the body raises, so exceptions never leave
    a widget mute.
    """
    prior: list[tuple[QObject, bool]] = []
    try:
        for w in widgets:
            if w is None:
                continue
            prior.append((w, w.signalsBlocked()))
            w.blockSignals(True)
        yield
    finally:
        for w, was_blocked in prior:
            try:
                w.blockSignals(was_blocked)
            except RuntimeError:
                # Widget already destroyed.
                pass


def safe_disconnect(signal, slot=None) -> None:
    """Disconnect a slot (or all) without raising if not connected."""
    try:
        if slot is None:
            signal.disconnect()
        else:
            signal.disconnect(slot)
    except (TypeError, RuntimeError):
        pass


def disconnect_all(signals: Iterable) -> None:
    for sig in signals:
        safe_disconnect(sig)


def weak_method(method) -> Callable[..., Any]:
    """Wrap a bound method so it does not keep its owner alive.

    Useful for ``QAction.triggered.connect`` on menus that outlive the owner.
    The wrapper becomes a no-op once the owner is GC'd.
    """
    if not hasattr(method, "__self__") or not hasattr(method, "__func__"):
        return method
    ref = weakref.ref(method.__self__)
    func = method.__func__
    name = getattr(method, "__qualname__", str(method))

    def wrapper(*args, **kwargs):
        owner = ref()
        if owner is None:
            return None
        return func(owner, *args, **kwargs)

    wrapper.__qualname__ = f"weak({name})"
    return wrapper


__all__ = ["block_signals", "disconnect_all", "safe_disconnect", "weak_method"]
