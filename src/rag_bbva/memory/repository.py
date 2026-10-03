"""Repositorio del historial de conversaciones (patrón Repository).

El servicio RAG (M9) habla con la interfaz `ConversationRepository` y no sabe si el
historial vive en SQLite (`SqlAlchemyConversationRepository`) o en memoria
(`InMemoryConversationRepository`, para tests).

Contrato común a todas las implementaciones:
- Las conversaciones se crean de forma explícita con `create_conversation`. Un
  `conversation_id` inexistente **no** se crea al vuelo: leer o escribir en él lanza
  `ConversationNotFoundError` (la API lo traduce a 404). Así un ID mal copiado no abre
  en silencio una conversación vacía y sin contexto.
- Los mensajes se devuelven en orden cronológico (orden de inserción).
- `get_last_n(id, n)` devuelve los últimos `n` mensajes en orden cronológico; `n=0`
  devuelve una lista vacía.
- El título de la conversación es su primera pregunta del usuario (recortada).
- `add_turn` guarda la pregunta y la respuesta **juntas o ninguna** (ADR-014). Con
  `conversation_id=None` crea la conversación en la misma operación, así un turno
  fallido no deja conversaciones vacías.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from rag_bbva.exceptions import ConversationNotFoundError, HistoryError, MessageNotFoundError
from rag_bbva.memory.models import (
    Conversation,
    Feedback,
    Message,
    MessageMetrics,
    Role,
    SavedTurn,
    title_from_question,
    utc_now,
)

_ROLES: tuple[str, ...] = ("user", "assistant")
_FEEDBACKS: tuple[str | None, ...] = ("up", "down", None)


def validate_message(role: str, content: str) -> None:
    """Rechaza roles desconocidos y mensajes vacíos."""
    if role not in _ROLES:
        raise HistoryError("Rol de mensaje inválido", detail=f"{role!r}; use {list(_ROLES)}")
    if not content.strip():
        raise HistoryError("El contenido del mensaje está vacío")


def validate_window(n: int) -> None:
    """Rechaza ventanas negativas de historial."""
    if n < 0:
        raise HistoryError("La ventana de historial no puede ser negativa", detail=f"n={n}")


def validate_feedback(feedback: str | None) -> None:
    """Acepta `up`, `down` o `None` (quitar la valoración)."""
    if feedback not in _FEEDBACKS:
        raise HistoryError("Valoración inválida", detail=f"{feedback!r}; use 'up', 'down' o None")


def new_conversation_id() -> str:
    """Identificador de conversación: UUID4 en texto."""
    return str(uuid4())


class ConversationRepository(ABC):
    """Interfaz del historial de conversaciones."""

    @abstractmethod
    def create_conversation(self, title: str | None = None) -> Conversation:
        """Crea una conversación vacía y la devuelve."""

    @abstractmethod
    def get_conversation(self, conversation_id: str) -> Conversation | None:
        """Devuelve la conversación o `None` si no existe."""

    @abstractmethod
    def list_conversations(self, limit: int = 50) -> list[Conversation]:
        """Conversaciones más recientes primero (por `updated_at`)."""

    @abstractmethod
    def add_message(
        self,
        conversation_id: str,
        role: Role,
        content: str,
        *,
        sources: Sequence[dict[str, Any]] = (),
        metrics: MessageMetrics | None = None,
    ) -> Message:
        """Agrega un mensaje al final de la conversación y actualiza `updated_at`."""

    @abstractmethod
    def add_turn(
        self,
        conversation_id: str | None,
        question: str,
        answer: str,
        *,
        sources: Sequence[dict[str, Any]] = (),
        metrics: MessageMetrics | None = None,
    ) -> SavedTurn:
        """Guarda pregunta y respuesta en una sola operación atómica. Si
        `conversation_id` es `None`, crea la conversación en esa misma operación."""

    @abstractmethod
    def get_messages(self, conversation_id: str) -> list[Message]:
        """Todos los mensajes de la conversación en orden cronológico."""

    @abstractmethod
    def get_last_n(self, conversation_id: str, n: int) -> list[Message]:
        """Últimos `n` mensajes de la conversación en orden cronológico."""

    @abstractmethod
    def set_feedback(self, message_id: int, feedback: Feedback | None) -> Message:
        """Guarda (o quita, con `None`) la valoración 👍/👎 de una respuesta del asistente.
        Un id inexistente o de una pregunta del usuario lanza `MessageNotFoundError`."""

    def require_conversation(self, conversation_id: str) -> Conversation:
        """Devuelve la conversación o lanza `ConversationNotFoundError`."""
        conversacion = self.get_conversation(conversation_id)
        if conversacion is None:
            raise ConversationNotFoundError("La conversación no existe", detail=conversation_id)
        return conversacion


class InMemoryConversationRepository(ConversationRepository):
    """Implementación en memoria (tests y prototipos): se pierde al terminar el proceso."""

    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[int, Message] = {}
        self._next_id = 1

    def create_conversation(self, title: str | None = None) -> Conversation:
        """Crea una conversación vacía y la devuelve."""
        ahora = self._clock()
        conversacion = Conversation(
            id=new_conversation_id(), created_at=ahora, updated_at=ahora, title=title
        )
        self._conversations[conversacion.id] = conversacion
        return conversacion

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        """Devuelve la conversación o `None` si no existe."""
        return self._conversations.get(conversation_id)

    def list_conversations(self, limit: int = 50) -> list[Conversation]:
        """Conversaciones más recientes primero (por `updated_at`)."""
        ordenadas = sorted(self._conversations.values(), key=lambda c: c.updated_at, reverse=True)
        return ordenadas[:limit]

    def add_message(
        self,
        conversation_id: str,
        role: Role,
        content: str,
        *,
        sources: Sequence[dict[str, Any]] = (),
        metrics: MessageMetrics | None = None,
    ) -> Message:
        """Agrega un mensaje al final de la conversación y actualiza `updated_at`."""
        validate_message(role, content)
        conversacion = self.require_conversation(conversation_id)
        ahora = self._clock()
        mensaje = Message(
            id=self._next_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at=ahora,
            sources=[dict(s) for s in sources],
            metrics=metrics or MessageMetrics(),
        )
        self._messages[mensaje.id] = mensaje
        self._next_id += 1
        titulo = conversacion.title
        if titulo is None and role == "user":
            titulo = title_from_question(content)
        self._conversations[conversation_id] = conversacion.model_copy(
            update={"updated_at": ahora, "title": titulo}
        )
        return mensaje

    def add_turn(
        self,
        conversation_id: str | None,
        question: str,
        answer: str,
        *,
        sources: Sequence[dict[str, Any]] = (),
        metrics: MessageMetrics | None = None,
    ) -> SavedTurn:
        """Guarda pregunta y respuesta juntas: valida todo antes de modificar nada."""
        validate_message("user", question)
        validate_message("assistant", answer)
        if conversation_id is not None:
            self.require_conversation(conversation_id)
        else:
            conversation_id = self.create_conversation().id
        pregunta = self.add_message(conversation_id, "user", question)
        respuesta = self.add_message(
            conversation_id, "assistant", answer, sources=sources, metrics=metrics
        )
        return SavedTurn(
            conversation=self.require_conversation(conversation_id),
            question=pregunta,
            answer=respuesta,
        )

    def get_messages(self, conversation_id: str) -> list[Message]:
        """Todos los mensajes de la conversación en orden cronológico."""
        self.require_conversation(conversation_id)
        return [m for m in self._messages.values() if m.conversation_id == conversation_id]

    def get_last_n(self, conversation_id: str, n: int) -> list[Message]:
        """Últimos `n` mensajes de la conversación en orden cronológico."""
        validate_window(n)
        mensajes = self.get_messages(conversation_id)
        return mensajes[-n:] if n else []

    def set_feedback(self, message_id: int, feedback: Feedback | None) -> Message:
        """Guarda (o quita, con `None`) la valoración 👍/👎 de una respuesta."""
        validate_feedback(feedback)
        existente = self._messages.get(message_id)
        if existente is None or existente.role != "assistant":
            raise MessageNotFoundError("La respuesta no existe", detail=str(message_id))
        mensaje = self._messages[message_id].model_copy(update={"feedback": feedback})
        self._messages[message_id] = mensaje
        return mensaje
