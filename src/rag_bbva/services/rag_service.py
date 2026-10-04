"""Servicio RAG (patrón Facade): un único punto de entrada para conversar.

`RAGService.ask(conversation_id, question)` orquesta todo el turno:

1. Historial: últimos `HISTORY_WINDOW_N` mensajes de la conversación (M8).
2. Reformulación de la pregunta según `QUERY_REWRITE_MODE` (M7).
3. Recuperación en Qdrant + reranking + umbral de "sin información" (M6).
4. Generación con citas (M7); con `no_answer` no se llama al LLM.
5. Persistencia **atómica** de pregunta y respuesta con sus métricas (ADR-014).

La API (M9), la UI (M10) y la CLI hablan solo con esta fachada.
"""

import logging
import time
from collections.abc import Callable

from pydantic import BaseModel

from rag_bbva.exceptions import LLMBudgetExceededError, LLMError
from rag_bbva.llm import budget
from rag_bbva.llm.generator import AnswerGenerator
from rag_bbva.llm.rewriter import QueryRewriter
from rag_bbva.memory.models import Conversation, Feedback, Message, MessageMetrics
from rag_bbva.memory.repository import ConversationRepository
from rag_bbva.retrieval.retriever import Retriever

logger = logging.getLogger(__name__)


class SourceRef(BaseModel):
    """Fuente citada: número de la cita [n], URL y título de la página."""

    n: int
    url: str
    title: str | None = None


class Timings(BaseModel):
    """Latencia por etapa del turno, en milisegundos."""

    rewrite: float
    retrieval: float
    rerank: float
    llm: float
    total: float


class TokenUsage(BaseModel):
    """Tokens del turno: reformulación + respuesta."""

    prompt: int
    completion: int
    total: int


class ChatResult(BaseModel):
    """Resultado de un turno de conversación."""

    conversation_id: str
    message_id: int  # id de la respuesta (para la valoración 👍/👎)
    question_message_id: int
    answer: str
    sources: list[SourceRef]
    no_answer: bool  # umbral duro o abstención del LLM con [SIN_INFO] (ADR-016)
    gray_zone: bool  # el #1 del reranker cayó entre el umbral duro y RERANK_MIN_SCORE
    rewritten_query: str | None  # None si la pregunta se buscó tal cual
    timings: Timings
    tokens: TokenUsage
    model: str | None  # None si no se llamó al LLM para responder
    prompt_version: str


def _ms(segundos: float) -> float:
    return round(segundos * 1000, 1)


class RAGService:
    """Fachada del asistente: historial → reformulación → recuperación → respuesta."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        retriever: Retriever,
        rewriter: QueryRewriter,
        generator: AnswerGenerator,
        history_window_n: int,
        turn_budget_seconds: float | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.repository = repository
        self.retriever = retriever
        self.rewriter = rewriter
        self.generator = generator
        self.history_window_n = history_window_n
        self.turn_budget_seconds = turn_budget_seconds
        self._clock = clock

    def warm_up(self) -> None:
        """Carga embedder y reranker antes de la primera pregunta."""
        self.retriever.warm_up()

    def ask(self, conversation_id: str | None, question: str) -> ChatResult:
        """Responde una pregunta dentro del presupuesto de tiempo del turno.

        Si el LLM falla porque se agotó `LLM_TURN_BUDGET_SECONDS`, se lanza
        `LLMBudgetExceededError` ("el servicio está lento") y no se guarda nada.
        """
        with budget.turn_budget(self.turn_budget_seconds):
            try:
                return self._ask(conversation_id, question)
            except LLMError as exc:
                if budget.exhausted() and not isinstance(exc, LLMBudgetExceededError):
                    raise budget.budget_exceeded_error(str(exc)) from exc
                raise

    def _ask(self, conversation_id: str | None, question: str) -> ChatResult:
        """Responde una pregunta dentro de una conversación.

        - `conversation_id=None` crea una conversación nueva al guardar el turno.
        - Un ID inexistente lanza `ConversationNotFoundError` antes de gastar tokens.
        - Si falla la recuperación o el LLM, la excepción se propaga y no se guarda nada:
          ni la pregunta ni la conversación nueva (ADR-014).
        """
        inicio = self._clock()
        historial: list[dict[str, str]] = []
        if conversation_id is not None:
            self.repository.require_conversation(conversation_id)
            ventana = self.repository.get_last_n(conversation_id, self.history_window_n)
            historial = [m.as_chat_message() for m in ventana]

        t0 = self._clock()
        reformulada = self.rewriter.rewrite(question, historial)
        t_rewrite = self._clock() - t0

        recuperacion = self.retriever.retrieve(reformulada.query)
        autonoma = reformulada.query if reformulada.used_llm else None
        respuesta = self.generator.generate(question, recuperacion, autonoma)

        fuentes = [SourceRef(n=s.number, url=s.url, title=s.title) for s in respuesta.sources]
        tokens_prompt = reformulada.prompt_tokens + respuesta.prompt_tokens
        tokens_salida = reformulada.completion_tokens + respuesta.completion_tokens
        total_antes_de_guardar = self._clock() - inicio
        metricas = MessageMetrics(
            retrieval_ms=recuperacion.retrieval_ms,
            rerank_ms=recuperacion.rerank_ms,
            llm_ms=respuesta.llm_ms,
            total_ms=_ms(total_antes_de_guardar),
            top_score=recuperacion.top_score,
            no_answer=respuesta.no_answer,
            prompt_tokens=tokens_prompt,
            completion_tokens=tokens_salida,
        )
        turno = self.repository.add_turn(
            conversation_id,
            question,
            respuesta.text,
            sources=[f.model_dump() for f in fuentes],
            metrics=metricas,
        )
        total = self._clock() - inicio
        resultado = ChatResult(
            conversation_id=turno.conversation.id,
            message_id=turno.answer.id,
            question_message_id=turno.question.id,
            answer=respuesta.text,
            sources=fuentes,
            no_answer=respuesta.no_answer,
            gray_zone=recuperacion.gray_zone,
            rewritten_query=autonoma,
            timings=Timings(
                rewrite=_ms(t_rewrite),
                retrieval=recuperacion.retrieval_ms,
                rerank=recuperacion.rerank_ms,
                llm=respuesta.llm_ms,
                total=_ms(total),
            ),
            tokens=TokenUsage(
                prompt=tokens_prompt,
                completion=tokens_salida,
                total=tokens_prompt + tokens_salida,
            ),
            model=respuesta.model,
            prompt_version=respuesta.prompt_version,
        )
        logger.info(
            "Turno respondido",
            extra={
                "conversation_id": resultado.conversation_id,
                "message_id": resultado.message_id,
                "historial": len(historial),
                "reformulada": autonoma is not None,
                "no_answer": resultado.no_answer,
                "zona_gris": resultado.gray_zone,
                "fuentes": len(fuentes),
                "total_ms": resultado.timings.total,
            },
        )
        return resultado

    def list_conversations(self, limit: int = 50) -> list[Conversation]:
        """Conversaciones más recientes primero."""
        return self.repository.list_conversations(limit=limit)

    def get_conversation(self, conversation_id: str) -> tuple[Conversation, list[Message]]:
        """Conversación y todos sus mensajes; `ConversationNotFoundError` si no existe."""
        conversacion = self.repository.require_conversation(conversation_id)
        return conversacion, self.repository.get_messages(conversation_id)

    def set_feedback(self, message_id: int, value: Feedback | None) -> Message:
        """Valoración 👍/👎 de una respuesta."""
        return self.repository.set_feedback(message_id, value)
