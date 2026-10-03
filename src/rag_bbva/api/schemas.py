"""Esquemas de entrada y salida de la API (M9)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from rag_bbva.memory.models import Conversation, Message, MessageMetrics


class ChatRequest(BaseModel):
    """Pregunta del usuario. Sin `conversation_id` se crea una conversación nueva."""

    question: str = Field(description="Pregunta en lenguaje natural.")
    conversation_id: str | None = Field(
        default=None,
        max_length=64,
        description="ID devuelto por un `/chat` anterior para continuar la conversación.",
    )

    @field_validator("question")
    @classmethod
    def _sin_vacias(cls, valor: str) -> str:
        """Quita espacios de los extremos y rechaza preguntas vacías."""
        limpia = valor.strip()
        if not limpia:
            raise ValueError("La pregunta no puede estar vacía")
        return limpia


class FeedbackRequest(BaseModel):
    """Valoración de una respuesta del asistente."""

    value: Literal["up", "down"]


class FeedbackResponse(BaseModel):
    message_id: int
    feedback: Literal["up", "down"] | None


class ConversationOut(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, conversacion: Conversation) -> "ConversationOut":
        return cls(**conversacion.model_dump())


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    sources: list[dict[str, Any]]
    metrics: MessageMetrics
    feedback: Literal["up", "down"] | None

    @classmethod
    def from_model(cls, mensaje: Message) -> "MessageOut":
        return cls(**mensaje.model_dump(exclude={"conversation_id"}))


class ConversationMessagesOut(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class ErrorResponse(BaseModel):
    """Cuerpo de todo error de la API: mensaje para el usuario y detalle opcional."""

    error: str
    detail: str | None = None
