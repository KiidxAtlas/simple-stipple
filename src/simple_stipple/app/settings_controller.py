from __future__ import annotations

from typing import TYPE_CHECKING

from simple_stipple.platform.settings import save_settings, settings_bus

if TYPE_CHECKING:
    from simple_stipple.app.pages import PageRuntime


class SettingsController:
    """Own persistence and canvas fan-out for application settings."""

    def __init__(self, settings: dict, page_runtime: PageRuntime, *, source: object) -> None:
        self.settings = settings
        self._page_runtime = page_runtime
        self._source = source

    def replace(self, settings: dict) -> None:
        previous = dict(self.settings)
        incoming = dict(settings)
        self.settings.clear()
        self.settings.update(incoming)
        self._page_runtime.apply_all(self.settings)
        for key, value in self.settings.items():
            if previous.get(key) != value:
                settings_bus.publish(key, value, self._source)

    def update(self, key: str, value) -> None:
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        save_settings(self.settings)
        self._page_runtime.apply(key, value)
        settings_bus.publish(key, value, self._source)
