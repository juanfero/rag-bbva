"""Presupuesto de tiempo de un turno de conversación (M10, ADR-017).

`RAGService.ask` abre `turn_budget(LLM_TURN_BUDGET_SECONDS)`; dentro, cada llamada al
LLM usa como timeout `min(LLM_TIMEOUT_SECONDS, tiempo restante)` y los reintentos no
esperan más allá del plazo. Si el plazo se agota, se lanza `LLMBudgetExceededError`
con un mensaje amigable y el turno no se guarda (ADR-014).

El plazo vive en una `ContextVar`: cada petición de la API (un hilo del threadpool)
tiene el suyo. Fuera de un turno (scripts, `llm-check`) no hay plazo.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from rag_bbva.exceptions import LLMBudgetExceededError

MENSAJE_LENTO = (
    "El servicio de respuestas está lento en este momento. Intenta de nuevo en unos minutos."
)

_plazo: ContextVar[float | None] = ContextVar("plazo_del_turno", default=None)


@contextmanager
def turn_budget(seconds: float | None) -> Iterator[None]:
    """Fija el plazo del turno (`None` = sin plazo) mientras dura el bloque."""
    token = _plazo.set(time.monotonic() + seconds if seconds else None)
    try:
        yield
    finally:
        _plazo.reset(token)


def remaining() -> float | None:
    """Segundos que le quedan al turno, o `None` si no hay plazo."""
    plazo = _plazo.get()
    return None if plazo is None else plazo - time.monotonic()


def exhausted() -> bool:
    """¿Se agotó el plazo del turno?"""
    restante = remaining()
    return restante is not None and restante <= 0


def budget_exceeded_error(detail: str | None = None) -> LLMBudgetExceededError:
    return LLMBudgetExceededError(MENSAJE_LENTO, detail=detail)


def call_timeout(default: float) -> float:
    """Timeout para la próxima llamada: el configurado, recortado al plazo restante."""
    restante = remaining()
    if restante is None:
        return default
    if restante <= 0:
        raise budget_exceeded_error("sin tiempo para llamar al LLM")
    return min(default, restante)
