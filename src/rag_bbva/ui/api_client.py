"""Cliente HTTP de la API (M10). Es lo único que la interfaz usa para hablar con el
sistema: la UI no importa el núcleo (servicios, recuperación, LLM ni historial).

Los modelos de este módulo reflejan las respuestas JSON de la API (M9). Todo error se
traduce a `ApiClientError` con un mensaje para mostrar al usuario.
"""

import logging
from datetime import date, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError

from rag_bbva.exceptions import ApiClientError

logger = logging.getLogger(__name__)

Feedback = Literal["up", "down"]


class Source(BaseModel):
    n: int
    url: str
    title: str | None = None


class Timings(BaseModel):
    rewrite: float
    retrieval: float
    rerank: float
    llm: float
    total: float


class Tokens(BaseModel):
    prompt: int
    completion: int
    total: int


class ChatReply(BaseModel):
    """Respuesta de `POST /chat`."""

    conversation_id: str
    message_id: int
    question_message_id: int
    answer: str
    sources: list[Source]
    no_answer: bool
    gray_zone: bool = False
    rewritten_query: str | None = None
    timings: Timings
    tokens: Tokens
    model: str | None = None
    prompt_version: str | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class StoredMessage(BaseModel):
    """Mensaje del historial (`GET /conversations/{id}/messages`)."""

    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    sources: list[dict[str, Any]]
    metrics: dict[str, Any]
    feedback: Feedback | None = None


class ConversationDetail(BaseModel):
    conversation: ConversationSummary
    messages: list[StoredMessage]


class ComponentStatus(BaseModel):
    status: str
    detail: str | None = None


class LLMStatus(ComponentStatus):
    provider: str | None = None
    model: str | None = None
    fallback_model: str | None = None


class HealthStatus(BaseModel):
    status: str
    qdrant: ComponentStatus
    sqlite: ComponentStatus
    llm: LLMStatus


def _mensaje_de_error(respuesta: httpx.Response) -> tuple[str, str | None]:
    """`(error, detail)` del cuerpo JSON de la API, o un texto genérico si no lo es."""
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return f"La API respondió {respuesta.status_code}", None
    if isinstance(cuerpo, dict) and "error" in cuerpo:
        return str(cuerpo["error"]), cuerpo.get("detail")
    return f"La API respondió {respuesta.status_code}", None


class ApiClient:
    """Cliente síncrono de la API del asistente."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 180,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def _pedir(self, metodo: str, ruta: str, **kwargs: Any) -> Any:
        try:
            respuesta = self._http.request(metodo, ruta, **kwargs)
        except httpx.TimeoutException as exc:
            raise ApiClientError(
                "La respuesta está tardando demasiado. El servicio de respuestas puede estar "
                "saturado; intenta de nuevo en unos minutos.",
                detail=str(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiClientError(
                f"No se pudo conectar con la API en {self.base_url}. ¿Está levantada? "
                "Inicie `python -m rag_bbva.cli serve` y revise API_BASE_URL.",
                detail=str(exc),
            ) from exc
        if respuesta.status_code >= 400:
            error, detalle = _mensaje_de_error(respuesta)
            logger.warning(
                "La API respondió con error",
                extra={"ruta": ruta, "status": respuesta.status_code, "error": error},
            )
            raise ApiClientError(error, status=respuesta.status_code, detail=detalle)
        return respuesta.json()

    def _modelo(self, clase: type[BaseModel], datos: Any) -> Any:
        try:
            return clase.model_validate(datos)
        except ValidationError as exc:
            raise ApiClientError(
                "La API devolvió una respuesta inesperada.", detail=str(exc)
            ) from exc

    def chat(self, question: str, conversation_id: str | None = None) -> ChatReply:
        """Envía una pregunta; sin `conversation_id` la API abre una conversación."""
        cuerpo: dict[str, Any] = {"question": question}
        if conversation_id:
            cuerpo["conversation_id"] = conversation_id
        return self._modelo(ChatReply, self._pedir("POST", "/chat", json=cuerpo))  # type: ignore[no-any-return]

    def list_conversations(self, limit: int = 20) -> list[ConversationSummary]:
        datos = self._pedir("GET", "/conversations", params={"limit": limit})
        return [self._modelo(ConversationSummary, d) for d in datos]

    def get_conversation(self, conversation_id: str) -> ConversationDetail:
        datos = self._pedir("GET", f"/conversations/{conversation_id}/messages")
        return self._modelo(ConversationDetail, datos)  # type: ignore[no-any-return]

    def send_feedback(self, message_id: int, value: Feedback) -> None:
        self._pedir("POST", f"/messages/{message_id}/feedback", json={"value": value})

    def analytics_summary(self, since: date | None = None) -> dict[str, Any]:
        """Resumen de `GET /analytics/summary` (JSON tal cual: la UI no importa el núcleo)."""
        parametros = {"since": since.isoformat()} if since else None
        datos = self._pedir("GET", "/analytics/summary", params=parametros)
        if not isinstance(datos, dict) or "operational" not in datos:
            raise ApiClientError("La API devolvió una respuesta inesperada.")
        return datos

    def health(self) -> HealthStatus:
        """Estado de la API. Un 503 de `/health` trae el reporte degradado en el cuerpo:
        se devuelve igual, no es un error para la UI."""
        try:
            respuesta = self._http.get("/health")
        except httpx.HTTPError as exc:
            raise ApiClientError(
                f"No se pudo conectar con la API en {self.base_url}.", detail=str(exc)
            ) from exc
        if respuesta.status_code not in (200, 503):
            error, detalle = _mensaje_de_error(respuesta)
            raise ApiClientError(error, status=respuesta.status_code, detail=detalle)
        return self._modelo(HealthStatus, respuesta.json())  # type: ignore[no-any-return]

    def close(self) -> None:
        self._http.close()
