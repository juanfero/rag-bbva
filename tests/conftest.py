"""Fixtures compartidas por toda la suite."""

from collections.abc import Iterator

import pytest

from rag_bbva.config import Settings, get_settings


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Elimina del entorno toda variable que pueda alterar `Settings`."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()
