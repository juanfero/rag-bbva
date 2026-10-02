"""Pruebas de la jerarquía de excepciones (M0)."""

import pytest

from rag_bbva import exceptions as exc

SUBCLASES = [
    exc.ConfigurationError,
    exc.ScrapingError,
    exc.ProcessingError,
    exc.IndexingError,
    exc.RetrievalError,
    exc.LLMError,
    exc.HistoryError,
]


@pytest.mark.parametrize("cls", SUBCLASES)
def test_exceptions_hierarchy(cls: type[exc.RagBbvaError]) -> None:
    """Toda excepción del dominio hereda de `RagBbvaError` y se captura con ella."""
    assert issubclass(cls, exc.RagBbvaError)
    with pytest.raises(exc.RagBbvaError):
        raise cls("falló")


def test_base_hereda_de_exception() -> None:
    """La base es una `Exception` estándar (no `BaseException`)."""
    assert issubclass(exc.RagBbvaError, Exception)


def test_mensaje_y_detalle() -> None:
    """El error conserva mensaje y detalle técnico por separado."""
    error = exc.LLMError("El asistente no está disponible", detail="HTTP 503")

    assert error.message == "El asistente no está disponible"
    assert error.detail == "HTTP 503"
    assert str(error) == "El asistente no está disponible (HTTP 503)"
    assert str(exc.ScrapingError("sin detalle")) == "sin detalle"
