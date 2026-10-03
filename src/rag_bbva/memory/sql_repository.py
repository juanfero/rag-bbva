"""Historial de conversaciones en SQLite con SQLAlchemy 2.0 (patrón Repository).

Persiste en `HISTORY_DB_PATH`: sobrevive a reinicios del proceso y del contenedor
(volumen `./data`). Las tablas se crean al construir el repositorio si no existen.

Concurrencia (M9): la API atiende peticiones en varios hilos. Cada operación abre su
propia sesión del pool; las conexiones se pueden usar desde cualquier hilo
(`check_same_thread=False`), SQLite espera hasta `_ESPERA_BLOQUEO_S` si otra escritura
tiene la base bloqueada y el modo WAL deja leer mientras se escribe.
"""

import json
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

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
from rag_bbva.memory.repository import (
    ConversationRepository,
    new_conversation_id,
    validate_feedback,
    validate_message,
    validate_window,
)

logger = logging.getLogger(__name__)

# Segundos que una escritura espera a que se libere el bloqueo de otra antes de fallar.
_ESPERA_BLOQUEO_S = 15


class _Base(DeclarativeBase):
    pass


class _ConversationRow(_Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str | None] = mapped_column(String(200))


class _MessageRow(_Base):
    __tablename__ = "messages"

    # Autoincremental: define el orden cronológico aunque dos mensajes compartan hora.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    retrieval_ms: Mapped[float | None] = mapped_column(Float)
    rerank_ms: Mapped[float | None] = mapped_column(Float)
    llm_ms: Mapped[float | None] = mapped_column(Float)
    total_ms: Mapped[float | None] = mapped_column(Float)
    top_score: Mapped[float | None] = mapped_column(Float)
    no_answer: Mapped[bool | None] = mapped_column(Boolean)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str | None] = mapped_column(String(8))


def _utc(valor: datetime) -> datetime:
    """SQLite no guarda la zona horaria: las fechas se escriben y se leen en UTC."""
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)


def _a_conversacion(fila: _ConversationRow) -> Conversation:
    return Conversation(
        id=fila.id,
        created_at=_utc(fila.created_at),
        updated_at=_utc(fila.updated_at),
        title=fila.title,
    )


def _a_mensaje(fila: _MessageRow) -> Message:
    return Message(
        id=fila.id,
        conversation_id=fila.conversation_id,
        role=fila.role,  # type: ignore[arg-type]  # validado al escribir
        content=fila.content,
        created_at=_utc(fila.created_at),
        sources=json.loads(fila.sources_json),
        metrics=MessageMetrics(
            retrieval_ms=fila.retrieval_ms,
            rerank_ms=fila.rerank_ms,
            llm_ms=fila.llm_ms,
            total_ms=fila.total_ms,
            top_score=fila.top_score,
            no_answer=fila.no_answer,
            prompt_tokens=fila.prompt_tokens,
            completion_tokens=fila.completion_tokens,
        ),
        feedback=fila.feedback,  # type: ignore[arg-type]  # validado al escribir
    )


def _activar_claves_foraneas(conexion: Any, _registro: Any) -> None:
    """SQLite ignora las claves foráneas salvo que se activen en cada conexión."""
    cursor = conexion.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _activar_wal(conexion: Any, _registro: Any) -> None:
    """Modo WAL: las lecturas no esperan a las escrituras (solo para archivos)."""
    cursor = conexion.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


class SqlAlchemyConversationRepository(ConversationRepository):
    """Historial persistente en una base SQL (SQLite por defecto)."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utc_now) -> None:
        self._engine = engine
        self._clock = clock
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        try:
            _Base.metadata.create_all(engine)
        except SQLAlchemyError as exc:
            mensaje = "No se pudo crear el esquema del historial"
            raise HistoryError(mensaje, detail=str(exc)) from exc

    @classmethod
    def from_path(
        cls, path: Path, clock: Callable[[], datetime] = utc_now
    ) -> "SqlAlchemyConversationRepository":
        """Repositorio sobre el archivo SQLite `path` (crea la carpeta si falta)."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            mensaje = "No se pudo crear la carpeta del historial"
            raise HistoryError(mensaje, detail=str(exc)) from exc
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": _ESPERA_BLOQUEO_S},
        )
        event.listen(engine, "connect", _activar_claves_foraneas)
        event.listen(engine, "connect", _activar_wal)
        logger.info("Historial de conversaciones en %s", path)
        return cls(engine, clock)

    @classmethod
    def in_memory(
        cls, clock: Callable[[], datetime] = utc_now
    ) -> "SqlAlchemyConversationRepository":
        """SQLite en memoria compartida por todas las sesiones (tests)."""
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        event.listen(engine, "connect", _activar_claves_foraneas)
        return cls(engine, clock)

    def close(self) -> None:
        """Cierra las conexiones del motor."""
        self._engine.dispose()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        """Sesión transaccional: confirma al salir y traduce errores a `HistoryError`."""
        try:
            with self._sessions.begin() as sesion:
                yield sesion
        except SQLAlchemyError as exc:
            raise HistoryError("Error de la base del historial", detail=str(exc)) from exc

    def _fila_conversacion(self, sesion: Session, conversation_id: str) -> _ConversationRow:
        fila = sesion.get(_ConversationRow, conversation_id)
        if fila is None:
            raise ConversationNotFoundError("La conversación no existe", detail=conversation_id)
        return fila

    def create_conversation(self, title: str | None = None) -> Conversation:
        """Crea una conversación vacía y la devuelve."""
        ahora = self._clock()
        fila = _ConversationRow(
            id=new_conversation_id(), created_at=ahora, updated_at=ahora, title=title
        )
        with self._session() as sesion:
            sesion.add(fila)
        return _a_conversacion(fila)

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        """Devuelve la conversación o `None` si no existe."""
        with self._session() as sesion:
            fila = sesion.get(_ConversationRow, conversation_id)
            return _a_conversacion(fila) if fila else None

    def list_conversations(self, limit: int = 50) -> list[Conversation]:
        """Conversaciones más recientes primero (por `updated_at`)."""
        consulta = (
            select(_ConversationRow).order_by(_ConversationRow.updated_at.desc()).limit(limit)
        )
        with self._session() as sesion:
            return [_a_conversacion(f) for f in sesion.scalars(consulta)]

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
        metricas = metrics or MessageMetrics()
        ahora = self._clock()
        with self._session() as sesion:
            conversacion = self._fila_conversacion(sesion, conversation_id)
            fila = _MessageRow(
                conversation_id=conversation_id,
                role=role,
                content=content,
                created_at=ahora,
                sources_json=json.dumps(list(sources), ensure_ascii=False),
                **metricas.model_dump(),
            )
            sesion.add(fila)
            conversacion.updated_at = ahora
            if conversacion.title is None and role == "user":
                conversacion.title = title_from_question(content)
            sesion.flush()
            return _a_mensaje(fila)

    def add_turn(
        self,
        conversation_id: str | None,
        question: str,
        answer: str,
        *,
        sources: Sequence[dict[str, Any]] = (),
        metrics: MessageMetrics | None = None,
    ) -> SavedTurn:
        """Guarda pregunta y respuesta en una sola transacción (y la conversación nueva,
        si `conversation_id` es `None`). Si algo falla, no queda nada guardado."""
        validate_message("user", question)
        validate_message("assistant", answer)
        metricas = metrics or MessageMetrics()
        fuentes_json = json.dumps(list(sources), ensure_ascii=False)
        ahora = self._clock()
        with self._session() as sesion:
            if conversation_id is None:
                conversacion = _ConversationRow(
                    id=new_conversation_id(), created_at=ahora, updated_at=ahora, title=None
                )
                sesion.add(conversacion)
            else:
                conversacion = self._fila_conversacion(sesion, conversation_id)
            pregunta = _MessageRow(
                conversation_id=conversacion.id, role="user", content=question, created_at=ahora
            )
            respuesta = _MessageRow(
                conversation_id=conversacion.id,
                role="assistant",
                content=answer,
                created_at=ahora,
                sources_json=fuentes_json,
                **metricas.model_dump(),
            )
            sesion.add(pregunta)
            sesion.flush()  # asigna el id de la pregunta antes que el de la respuesta
            sesion.add(respuesta)
            conversacion.updated_at = ahora
            if conversacion.title is None:
                conversacion.title = title_from_question(question)
            sesion.flush()
            return SavedTurn(
                conversation=_a_conversacion(conversacion),
                question=_a_mensaje(pregunta),
                answer=_a_mensaje(respuesta),
            )

    def get_messages(self, conversation_id: str) -> list[Message]:
        """Todos los mensajes de la conversación en orden cronológico."""
        consulta = (
            select(_MessageRow)
            .where(_MessageRow.conversation_id == conversation_id)
            .order_by(_MessageRow.id)
        )
        with self._session() as sesion:
            self._fila_conversacion(sesion, conversation_id)
            return [_a_mensaje(f) for f in sesion.scalars(consulta)]

    def get_last_n(self, conversation_id: str, n: int) -> list[Message]:
        """Últimos `n` mensajes de la conversación en orden cronológico."""
        validate_window(n)
        consulta = (
            select(_MessageRow)
            .where(_MessageRow.conversation_id == conversation_id)
            .order_by(_MessageRow.id.desc())
            .limit(n)
        )
        with self._session() as sesion:
            self._fila_conversacion(sesion, conversation_id)
            if n == 0:
                return []
            filas = list(sesion.scalars(consulta))
        return [_a_mensaje(f) for f in reversed(filas)]

    def set_feedback(self, message_id: int, feedback: Feedback | None) -> Message:
        """Guarda (o quita, con `None`) la valoración 👍/👎 de una respuesta."""
        validate_feedback(feedback)
        with self._session() as sesion:
            fila = sesion.get(_MessageRow, message_id)
            if fila is None or fila.role != "assistant":
                raise MessageNotFoundError("La respuesta no existe", detail=str(message_id))
            fila.feedback = feedback
            sesion.flush()
            return _a_mensaje(fila)
