"""Generación de la respuesta con citas (M7).

Si el retriever marcó `no_answer` (umbral de M6), no se llama al LLM: se devuelve una
respuesta fija y se ahorran tokens. Si no, se arma el prompt con el contexto numerado,
se llama al proveedor y se post-procesan las citas.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from pydantic import BaseModel

from rag_bbva.llm.citations import Source, process_citations
from rag_bbva.llm.prompts import NO_ANSWER_MESSAGE, PROMPT_VERSION, build_answer_messages
from rag_bbva.llm.provider import LLMProvider
from rag_bbva.retrieval.models import RetrievalResult


class Answer(BaseModel):
    """Respuesta final con fuentes, tokens y métricas de la generación."""

    text: str
    sources: list[Source]
    no_answer: bool
    llm_called: bool
    model: str | None
    prompt_version: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_ms: float = 0.0


@dataclass
class AnswerGenerator:
    """Arma el prompt, llama al LLM y deja las citas listas para mostrar."""

    llm: LLMProvider
    max_tokens: int | None = None

    def _sin_respuesta(self) -> Answer:
        return Answer(
            text=NO_ANSWER_MESSAGE,
            sources=[],
            no_answer=True,
            llm_called=False,
            model=None,
            prompt_version=PROMPT_VERSION,
        )

    def generate(
        self, question: str, retrieval: RetrievalResult, standalone_question: str | None = None
    ) -> Answer:
        """Respuesta completa (sin streaming). `standalone_question` es la pregunta
        reformulada con el historial, si la hubo (se agrega al prompt)."""
        if retrieval.no_answer or not retrieval.results:
            return self._sin_respuesta()
        respuesta = self.llm.complete(
            build_answer_messages(question, retrieval.results, standalone_question),
            max_tokens=self.max_tokens,
        )
        texto, fuentes = process_citations(respuesta.text, retrieval.results)
        return Answer(
            text=texto,
            sources=fuentes,
            no_answer=False,
            llm_called=True,
            model=respuesta.model,
            prompt_version=PROMPT_VERSION,
            prompt_tokens=respuesta.prompt_tokens,
            completion_tokens=respuesta.completion_tokens,
            llm_ms=respuesta.latency_ms,
        )

    def stream(
        self, question: str, retrieval: RetrievalResult, standalone_question: str | None = None
    ) -> tuple[Iterator[str], "StreamedAnswer"]:
        """Fragmentos de texto a medida que llegan y un objeto que, al terminar, tiene la
        respuesta final con las citas procesadas (para la UI de M10)."""
        resultado = StreamedAnswer()
        if retrieval.no_answer or not retrieval.results:
            resultado.answer = self._sin_respuesta()
            return iter([NO_ANSWER_MESSAGE]), resultado
        flujo = self.llm.stream(
            build_answer_messages(question, retrieval.results, standalone_question),
            max_tokens=self.max_tokens,
        )

        def fragmentos() -> Iterator[str]:
            yield from flujo
            final = flujo.response
            if final is None:  # el flujo se cortó antes de terminar
                return
            texto, fuentes = process_citations(final.text, retrieval.results)
            resultado.answer = Answer(
                text=texto,
                sources=fuentes,
                no_answer=False,
                llm_called=True,
                model=final.model,
                prompt_version=PROMPT_VERSION,
                prompt_tokens=final.prompt_tokens,
                completion_tokens=final.completion_tokens,
                llm_ms=final.latency_ms,
            )

        return fragmentos(), resultado


@dataclass
class StreamedAnswer:
    """Contenedor que recibe la respuesta final al terminar el streaming."""

    answer: Answer | None = None
