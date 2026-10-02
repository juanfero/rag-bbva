"""Pruebas de la configuración de logging (M0)."""

import json
import logging
import sys
from collections.abc import Iterator

import pytest

from rag_bbva.logging_conf import JsonFormatter, configure_logging


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    """Restaura handlers y nivel del logger raíz tras la prueba."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("rag_bbva.x", logging.WARNING, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emite_json_con_campos_base_y_extra() -> None:
    """Cada registro es JSON válido con los campos base y los `extra`."""
    data = json.loads(JsonFormatter().format(_record("hola ñandú", url="https://x", ms=12)))

    assert data["level"] == "WARNING"
    assert data["logger"] == "rag_bbva.x"
    assert data["message"] == "hola ñandú"
    assert data["url"] == "https://x"
    assert data["ms"] == 12
    assert "timestamp" in data


def test_json_formatter_incluye_excepcion() -> None:
    """Las trazas de excepción se serializan en el campo `exception`."""
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("falló")
        record.exc_info = sys.exc_info()

    data = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in data["exception"]


def test_configure_logging_usa_nivel_de_settings(
    clean_env: pytest.MonkeyPatch, restore_root_logger: None
) -> None:
    """Sin argumento, el nivel sale de `LOG_LEVEL`."""
    clean_env.setenv("LOG_LEVEL", "WARNING")

    configure_logging()

    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_configure_logging_nivel_explicito(restore_root_logger: None) -> None:
    """Un nivel explícito tiene prioridad y llamar dos veces no duplica handlers."""
    configure_logging("DEBUG")
    configure_logging("DEBUG")

    assert logging.getLogger().level == logging.DEBUG
    assert len(logging.getLogger().handlers) == 1
