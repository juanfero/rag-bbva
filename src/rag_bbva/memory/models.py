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
    """Métricas de una respuesta del asistente (todas opcionales; M9 y M11 las usan)."""

    model_config = ConfigDict(frozen=True)

    retrieval_ms: float | None = None
    rerank_ms: float | None = None
    llm_ms: float | None = None
    total_ms: float | None = None
    top_score: float | None = None
    no_answer: bool | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


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
