"""In-memory user-notification history."""

from __future__ import annotations

from collections import deque
from datetime import datetime

_notification_history: deque[tuple[str, str]] = deque(maxlen=200)


def record_notification(text: str) -> None:
    if text:
        _notification_history.append((datetime.now().strftime("%H:%M:%S"), str(text)))


def notification_history() -> list[tuple[str, str]]:
    return list(_notification_history)
