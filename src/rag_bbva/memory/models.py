"""Modelos del historial de conversaciones."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant"]
Feedback = Literal["up", "down"]

TITLE_MAX_CHARS = 80


def utc_now() -> datetime:
    """Reloj por defecto del repositorio: fecha y hora actual en UTC."""
    return datetime.now(UTC)


def title_from_question(question: str) -> str:
    """Título de la conversación: la primera pregunta, en una línea y recortada."""
    texto = " ".join(question.split())
    if len(texto) <= TITLE_MAX_CHARS:
        return texto
    return texto[: TITLE_MAX_CHARS - 1].rstrip() + "…"


class Conversation(BaseModel):
    """Conversación: agrupa los mensajes de un mismo hilo."""

    model_config = ConfigDict(frozen=True)

    id: str
    created_at: datetime
    updated_at: datetime
    title: str | None = None


class MessageMetrics(BaseModel):
    """Métricas de una respuesta del asistente (todas opcionales; M9 y M11 las usan).

    `rewrite_ms`, `rewritten_query`, `model` y `gray_zone` se agregan en M11 (ADR-018):
    en mensajes anteriores quedan en `None`.
    """

    model_config = ConfigDict(frozen=True)

    rewrite_ms: float | None = None
    retrieval_ms: float | None = None
    rerank_ms: float | None = None
    llm_ms: float | None = None
    total_ms: float | None = None
    top_score: float | None = None
    no_answer: bool | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Pregunta reformulada con el historial (None si se buscó tal cual).
    rewritten_query: str | None = None
    # Modelo que generó la respuesta (None si no se llamó al LLM para responder).
    model: str | None = None
    # El #1 del reranker cayó entre el umbral duro y RERANK_MIN_SCORE (ADR-016).
    gray_zone: bool | None = None


class Message(BaseModel):
    """Mensaje persistido de una conversación."""

    model_config = ConfigDict(frozen=True)

    id: int
    conversation_id: str
    role: Role
    content: str
    created_at: datetime
    sources: list[dict[str, Any]] = Field(default_factory=list)
    metrics: MessageMetrics = Field(default_factory=MessageMetrics)
    feedback: Feedback | None = None

    def as_chat_message(self) -> dict[str, str]:
        """Formato `{"role", "content"}` que reciben el LLM y el reformulador."""
        return {"role": self.role, "content": self.content}


class SavedTurn(BaseModel):
    """Turno guardado de forma atómica: la pregunta y la respuesta (M9, ADR-014)."""

    model_config = ConfigDict(frozen=True)

    conversation: Conversation
    question: Message
    answer: Message
