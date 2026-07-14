"""Process-wide live settings propagation for multi-window sessions."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class SettingsBus(QObject):
    changed = Signal(str, object, object)  # key, value, source token

    def publish(self, key: str, value: object, source: object) -> None:
        self.changed.emit(key, value, source)


settings_bus = SettingsBus()
