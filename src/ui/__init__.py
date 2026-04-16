"""Public UI package exports."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app import App as MainWindow

__all__ = ["MainWindow"]


def __getattr__(name: str):
    if name == "MainWindow":
        from src.app import App as MainWindow

        return MainWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
