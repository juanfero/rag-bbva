"""API REST del asistente (M9), construida con una fábrica (`create_app`).

Los componentes llegan por inyección de dependencias (`Depends`): en producción el
`lifespan` los crea desde la configuración y calienta los modelos; en los tests se pasan
servicios con dobles a `create_app` o se reemplazan con `app.dependency_overrides`.

Los endpoints son síncronos: FastAPI los ejecuta en su threadpool, así una pregunta que
espera al LLM no bloquea a las demás. El historial SQLite es seguro entre hilos (M9).
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rag_bbva import __version__
from rag_bbva.analytics.metrics import AnalyticsSummary
from rag_bbva.analytics.service import AnalyticsService
from rag_bbva.api.errors import register_error_handlers
from rag_bbva.api.schemas import (
    ChatRequest,
    ConversationMessagesOut,
    ConversationOut,
    ErrorResponse,
    FeedbackRequest,
    FeedbackResponse,
    MessageOut,
)
from rag_bbva.config import Settings, get_settings
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.services.health import HealthChecker, HealthReport
from rag_bbva.services.rag_service import ChatResult, RAGService

logger = logging.getLogger(__name__)

_ERRORES = {
    404: {"model": ErrorResponse, "description": "No encontrado"},
    422: {"model": ErrorResponse, "description": "Solicitud inválida"},
    503: {"model": ErrorResponse, "description": "LLM, búsqueda o historial no disponible"},
}


def get_app_settings(request: Request) -> Settings:
    """Configuración de la app."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_rag_service(request: Request) -> RAGService:
    """Servicio RAG de la app."""
    return request.app.state.rag_service  # type: ignore[no-any-return]


def get_health_checker(request: Request) -> HealthChecker:
    """Chequeo de salud de la app."""
    return request.app.state.health_checker  # type: ignore[no-any-return]


def get_analytics_service(request: Request) -> AnalyticsService:
    """Analítica del historial de la app (M11)."""
    return request.app.state.analytics_service  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ServiceDep = Annotated[RAGService, Depends(get_rag_service)]
HealthDep = Annotated[HealthChecker, Depends(get_health_checker)]
AnalyticsDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


def create_app(
    settings: Settings | None = None,
    *,
    service: RAGService | None = None,
    health_checker: HealthChecker | None = None,
    analytics_service: AnalyticsService | None = None,
    warm_up: bool = True,
) -> FastAPI:
    """Crea la app. Lo que no se pase se construye al arrancar desde `settings`."""
    ajustes = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        fabrica = ComponentFactory(ajustes)
        if app.state.rag_service is None:
            app.state.rag_service = fabrica.create_rag_service(tolerate_missing_llm=True)
        if app.state.health_checker is None:
            app.state.health_checker = fabrica.create_health_checker(app.state.rag_service)
        if app.state.analytics_service is None:
            # Mismo historial y mismo embedder (ya cargado) que el servicio RAG.
            app.state.analytics_service = fabrica.create_analytics_service(
                repository=app.state.rag_service.repository,
                embedder=app.state.rag_service.retriever.embedder,
            )
        if warm_up:
            inicio = time.perf_counter()
            app.state.rag_service.warm_up()
            logger.info(
                "Modelos cargados (embedder y reranker)",
                extra={"warm_up_ms": round((time.perf_counter() - inicio) * 1000, 1)},
            )
        yield

    app = FastAPI(
        title="Asistente Bancolombia (RAG)",
        version=__version__,
        description=(
            "Responde preguntas sobre el contenido público de www.bancolombia.com con "
            "citas a las páginas fuente y memoria por conversación."
        ),
        lifespan=lifespan,
    )
    app.state.settings = ajustes
    app.state.rag_service = service
    app.state.health_checker = health_checker
    app.state.analytics_service = analytics_service
    register_error_handlers(app)

    @app.post("/chat", response_model=ChatResult, responses=_ERRORES, tags=["chat"])
    def chat(body: ChatRequest, servicio: ServiceDep, config: SettingsDep) -> ChatResult:
        """Responde una pregunta. Sin `conversation_id` abre una conversación nueva; con
        un ID inexistente responde 404."""
        maximo = config.chat_question_max_chars
        if len(body.question) > maximo:
            raise RequestValidationError(
                [
                    {
                        "type": "string_too_long",
                        "loc": ("body", "question"),
                        "msg": f"La pregunta supera el máximo de {maximo} caracteres",
                    }
                ]
            )
        return servicio.ask(body.conversation_id, body.question)

    @app.get("/conversations", response_model=list[ConversationOut], tags=["historial"])
    def conversations(
        servicio: ServiceDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> list[ConversationOut]:
        """Conversaciones, la más reciente primero."""
        return [ConversationOut.from_model(c) for c in servicio.list_conversations(limit)]

    @app.get(
        "/conversations/{conversation_id}/messages",
        response_model=ConversationMessagesOut,
        responses=_ERRORES,
        tags=["historial"],
    )
    def messages(conversation_id: str, servicio: ServiceDep) -> ConversationMessagesOut:
        """Mensajes de una conversación en orden cronológico."""
        conversacion, mensajes = servicio.get_conversation(conversation_id)
        return ConversationMessagesOut(
            conversation=ConversationOut.from_model(conversacion),
            messages=[MessageOut.from_model(m) for m in mensajes],
        )

    @app.post(
        "/messages/{message_id}/feedback",
        response_model=FeedbackResponse,
        responses=_ERRORES,
        tags=["historial"],
    )
    def feedback(message_id: int, body: FeedbackRequest, servicio: ServiceDep) -> FeedbackResponse:
        """Valoración 👍 (`up`) o 👎 (`down`) de una respuesta del asistente."""
        mensaje = servicio.set_feedback(message_id, body.value)
        return FeedbackResponse(message_id=mensaje.id, feedback=mensaje.feedback)

    @app.get(
        "/analytics/summary",
        response_model=AnalyticsSummary,
        responses=_ERRORES,
        tags=["analítica"],
    )
    def analytics_summary(
        analitica: AnalyticsDep,
        config: SettingsDep,
        since: Annotated[
            date | None, Query(description="Desde esta fecha (AAAA-MM-DD, ANALYTICS_TIMEZONE).")
        ] = None,
    ) -> AnalyticsSummary:
        """Métricas del historial: operativas, calidad, contenido, memoria, costo e impacto
        estimado. `meta.demo` indica si la base es de demostración."""
        desde = (
            datetime(since.year, since.month, since.day, tzinfo=ZoneInfo(config.analytics_timezone))
            if since
            else None
        )
        return analitica.summary(desde)

    @app.get(
        "/health",
        response_model=HealthReport,
        responses={503: {"model": HealthReport, "description": "Alguna dependencia falla"}},
        tags=["operación"],
    )
    def health(checker: HealthDep) -> JSONResponse:
        """Estado de Qdrant, SQLite y la configuración del LLM (sin gastar tokens).
        200 si todo está bien; 503 con `status: degraded` si algo falla."""
        reporte = checker.check()
        codigo = 200 if reporte.status == "ok" else 503
        return JSONResponse(status_code=codigo, content=reporte.model_dump(mode="json"))

    return app
