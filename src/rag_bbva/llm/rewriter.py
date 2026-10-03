"""Reformulación de la pregunta antes de recuperar (M7).

`QUERY_REWRITE_MODE`:
- `off`: se busca con la pregunta tal cual.
- `history_only`: se reformula solo si hay historial (preguntas de seguimiento como
  "¿y su tasa?"); la primera pregunta de una conversación va tal cual. El historial lo
  entrega el servicio RAG (M9) con los últimos `HISTORY_WINDOW_N` mensajes.
- `always`: se reformula siempre, también para expandir siglas y términos coloquiales
  ("4 por mil" → GMF).

La pregunta reformulada se usa para recuperar. Al generar, el prompt lleva la pregunta
original del usuario y, si hubo reformulación, también la autónoma (M9): sin ella el
modelo no sabría a qué se refiere "¿y cuáles son los requisitos?".
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from rag_bbva.exceptions import LLMError
from rag_bbva.llm.prompts import build_rewrite_messages
from rag_bbva.llm.provider import LLMProvider, Message

logger = logging.getLogger(__name__)
_MAX_CARACTERES = 400


class RewriteResult(BaseModel):
    """Resultado de la reformulación."""

    original: str
    query: str  # la que se usa para recuperar
    used_llm: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


@dataclass
class QueryRewriter:
    """Convierte la pregunta en una consulta autónoma y expandida para el retriever."""

    llm: LLMProvider
    mode: str = "history_only"
    max_tokens: int = 120

    def rewrite(self, question: str, history: Sequence[Message] = ()) -> RewriteResult:
        """Pregunta para recuperar, según el modo y el historial."""
        if self.mode == "off" or (self.mode == "history_only" and not history):
            return RewriteResult(original=question, query=question, used_llm=False)
        try:
            respuesta = self.llm.complete(
                build_rewrite_messages(question, history), max_tokens=self.max_tokens
            )
        except LLMError as exc:
            # Si la reformulación falla, se busca con la pregunta original.
            logger.warning(
                "Reformulación fallida; se usa la pregunta original", extra={"error": str(exc)}
            )
            return RewriteResult(original=question, query=question, used_llm=False)
        reformulada = " ".join(respuesta.text.split()).strip("\"'«» ")[:_MAX_CARACTERES]
        return RewriteResult(
            original=question,
            query=reformulada or question,
            used_llm=True,
            prompt_tokens=respuesta.prompt_tokens,
            completion_tokens=respuesta.completion_tokens,
            latency_ms=respuesta.latency_ms,
        )
