from __future__ import annotations

import pytest

from simple_stipple.core.patterns.processing import (
    available_patterns,
    get_generator,
    register_generator,
    register_pattern,
)


def test_generator_registry_supports_extensions_without_overriding_builtins() -> None:
    def extension(*_args, **_kwargs):
        return []

    register_generator("test_extension_generator", extension)

    assert get_generator("test_extension_generator") is extension
    with pytest.raises(ValueError, match="already registered"):
        register_generator("test_extension_generator", extension)


def test_pattern_registry_exposes_an_extension_to_the_pattern_picker() -> None:
    register_pattern("Test extension pattern", lambda _outline, _params: [])

    assert "Test extension pattern" in available_patterns()
