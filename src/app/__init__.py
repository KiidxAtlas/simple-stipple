"""Application composition package."""

from src.infra.settings import save_settings

from .window import App

__all__ = ["App", "save_settings"]
