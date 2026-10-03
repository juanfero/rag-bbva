"""Manejadores globales de errores: siempre JSON `{error, detail}` y nunca trazas.

| Excepción | HTTP |
|---|---|
| Validación de la petición | 422 |
| `ConversationNotFoundError`, `MessageNotFoundError`, ruta inexistente | 404 |
| `LLMError` (cupo, timeout, clave inválida…), `ConfigurationError` | 503 |
| `IndexingError` / `RetrievalError` (Qdrant caído), `HistoryError` | 503 |
| Cualquier otra | 500 |
"""

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from rag_bbva.exceptions import (
    ConfigurationError,
    ConversationNotFoundError,
    HistoryError,
    IndexingError,
    LLMError,
    MessageNotFoundError,
    RagBbvaError,
    RetrievalError,
)

logger = logging.getLogger(__name__)

MENSAJE_BUSQUEDA_CAIDA = (
    "El servicio de búsqueda no está disponible en este momento. Intente de nuevo en unos minutos."
)
MENSAJE_HISTORIAL_CAIDO = "El historial de conversaciones no está disponible en este momento."
MENSAJE_INTERNO = "Ocurrió un error interno. Intente de nuevo más tarde."


def _json(status: int, error: str, detail: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


def describe_validation_errors(errores: Sequence[Any]) -> str:
    """`question: La pregunta no puede estar vacía` a partir de los errores de pydantic."""
    partes = []
    for error in errores:
        campo = ".".join(str(p) for p in error.get("loc", ()) if p not in ("body", "query"))
        mensaje = str(error.get("msg", "")).removeprefix("Value error, ")
        partes.append(f"{campo}: {mensaje}" if campo else mensaje)
    return "; ".join(partes)


def register_error_handlers(app: FastAPI) -> None:
    """Registra los manejadores en la app."""

    @app.exception_handler(RequestValidationError)
    def _validacion(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _json(422, "Solicitud inválida", describe_validation_errors(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        mensaje = "Recurso no encontrado" if exc.status_code == 404 else str(exc.detail)
        return _json(exc.status_code, mensaje)

    @app.exception_handler(ConversationNotFoundError)
    @app.exception_handler(MessageNotFoundError)
    def _no_encontrado(_request: Request, exc: RagBbvaError) -> JSONResponse:
        return _json(404, exc.message, exc.detail)

    @app.exception_handler(LLMError)
    def _llm(_request: Request, exc: LLMError) -> JSONResponse:
        # El mensaje ya es amigable (M7); el detalle técnico del proveedor solo va al log.
        logger.warning("LLM no disponible", extra={"error": str(exc)})
        return _json(503, exc.message)

    @app.exception_handler(ConfigurationError)
    def _configuracion(_request: Request, exc: ConfigurationError) -> JSONResponse:
        logger.error("Configuración incompleta", extra={"error": str(exc)})
        return _json(503, "El asistente no está configurado por completo", exc.message)

    @app.exception_handler(IndexingError)
    @app.exception_handler(RetrievalError)
    def _busqueda(_request: Request, exc: RagBbvaError) -> JSONResponse:
        logger.warning("Búsqueda no disponible", extra={"error": str(exc)})
        return _json(503, MENSAJE_BUSQUEDA_CAIDA)

    @app.exception_handler(HistoryError)
    def _historial(_request: Request, exc: HistoryError) -> JSONResponse:
        logger.error("Historial no disponible", extra={"error": str(exc)})
        return _json(503, MENSAJE_HISTORIAL_CAIDO)

    @app.exception_handler(Exception)
    def _interno(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Error no controlado en la API", exc_info=exc)
        return _json(500, MENSAJE_INTERNO)
