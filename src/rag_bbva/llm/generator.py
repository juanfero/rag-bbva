"""Generación de la respuesta con citas (M7) y umbral doble (M9, ADR-016).

- Por debajo del umbral duro (`hard_no_answer`) no se llama al LLM: se devuelve una
  respuesta fija y se ahorran tokens.
- En la zona gris y por encima del umbral se arma el prompt con el contexto numerado y
  se llama al proveedor. Si el LLM se abstiene, empieza con `[SIN_INFO]`: la marca se
  quita del texto y la respuesta queda como `no_answer`.
- Después se post-procesan las citas.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from pydantic import BaseModel

from rag_bbva.llm.citations import Source, process_citations
from rag_bbva.llm.prompts import (
    ABSTENTION_MARKER,
    NO_ANSWER_MESSAGE,
    PROMPT_VERSION,
    build_answer_messages,
)
from rag_bbva.llm.provider import LLMProvider, LLMResponse
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
    gray_zone: bool = False  # el contexto estaba en la zona gris del umbral (ADR-016)
    abstained: bool = False  # el LLM se abstuvo con la marca [SIN_INFO]


def strip_abstention(texto: str) -> tuple[str, bool]:
    """Quita la marca de abstención y dice si la respuesta se abstiene.

    Solo es abstención si la respuesta **empieza** con la marca (lo que pide el prompt).
    Una marca a mitad de texto (p. ej. tras una respuesta parcial con citas, como pasó
    en M10) se borra sin marcar `no_answer`: la respuesta sí respondió. Si no queda
    texto, se usa el mensaje fijo de "sin información".
    """
    if ABSTENTION_MARKER not in texto:
        return texto, False
    abstenida = texto.lstrip().startswith(ABSTENTION_MARKER)
    limpio = texto.replace(ABSTENTION_MARKER, "").strip()
    return (limpio or NO_ANSWER_MESSAGE), abstenida or not limpio


def _filtrar_marca(fragmentos: Iterator[str]) -> Iterator[str]:
    """Quita la marca de abstención del streaming: retiene el comienzo hasta saber si
    empieza con la marca."""
    pendiente = ""
    decidido = False
    for fragmento in fragmentos:
        if decidido:
            yield fragmento.replace(ABSTENTION_MARKER, "")
            continue
        pendiente += fragmento
        inicio = pendiente.lstrip()
        if ABSTENTION_MARKER.startswith(inicio):
            continue  # todavía puede ser la marca
        decidido = True
        if inicio.startswith(ABSTENTION_MARKER):
            pendiente = inicio.removeprefix(ABSTENTION_MARKER).lstrip()
        if pendiente:
            yield pendiente.replace(ABSTENTION_MARKER, "")
    if not decidido and pendiente.strip() != ABSTENTION_MARKER and pendiente.strip():
        yield pendiente


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

    @staticmethod
    def _llama_al_llm(retrieval: RetrievalResult) -> bool:
        return bool(retrieval.results) and not retrieval.hard_no_answer

    def _respuesta(self, texto_llm: str, retrieval: RetrievalResult, final: LLMResponse) -> Answer:
        texto, abstenida = strip_abstention(texto_llm)
        texto, fuentes = process_citations(texto, retrieval.results)
        return Answer(
            text=texto,
            sources=fuentes,
            no_answer=abstenida,
            llm_called=True,
            model=final.model,
            prompt_version=PROMPT_VERSION,
            prompt_tokens=final.prompt_tokens,
            completion_tokens=final.completion_tokens,
            llm_ms=final.latency_ms,
            gray_zone=retrieval.gray_zone,
            abstained=abstenida,
        )

    def generate(
        self, question: str, retrieval: RetrievalResult, standalone_question: str | None = None
    ) -> Answer:
        """Respuesta completa (sin streaming). `standalone_question` es la pregunta
        reformulada con el historial, si la hubo (se agrega al prompt)."""
        if not self._llama_al_llm(retrieval):
            return self._sin_respuesta()
        respuesta = self.llm.complete(
            build_answer_messages(question, retrieval.results, standalone_question),
            max_tokens=self.max_tokens,
        )
        return self._respuesta(respuesta.text, retrieval, respuesta)

    def stream(
        self, question: str, retrieval: RetrievalResult, standalone_question: str | None = None
    ) -> tuple[Iterator[str], "StreamedAnswer"]:
        """Fragmentos de texto a medida que llegan y un objeto que, al terminar, tiene la
        respuesta final con las citas procesadas (para la UI de M10)."""
        resultado = StreamedAnswer()
        if not self._llama_al_llm(retrieval):
            resultado.answer = self._sin_respuesta()
            return iter([NO_ANSWER_MESSAGE]), resultado
        flujo = self.llm.stream(
            build_answer_messages(question, retrieval.results, standalone_question),
            max_tokens=self.max_tokens,
        )

        def fragmentos() -> Iterator[str]:
            yield from _filtrar_marca(iter(flujo))
            final = flujo.response
            if final is None:  # el flujo se cortó antes de terminar
                return
            resultado.answer = self._respuesta(final.text, retrieval, final)

        return fragmentos(), resultado


@dataclass
class StreamedAnswer:
    """Contenedor que recibe la respuesta final al terminar el streaming."""

    answer: Answer | None = None
