"""Umbral doble y abstención del LLM (M9, ADR-016)."""

from collections.abc import Iterator, Sequence

import pytest
from pydantic import ValidationError

from rag_bbva.config import Settings
from rag_bbva.indexing.embedding import FakeEmbedder
from rag_bbva.indexing.vector_store import SearchHit
from rag_bbva.llm.generator import AnswerGenerator, _filtrar_marca, strip_abstention
from rag_bbva.llm.prompts import ABSTENTION_MARKER, NO_ANSWER_MESSAGE, SYSTEM_PROMPT
from rag_bbva.llm.provider import FakeLLMProvider
from rag_bbva.memory.repository import InMemoryConversationRepository
from rag_bbva.retrieval.models import Candidate, RetrievalResult
from rag_bbva.retrieval.reranker import NoOpReranker, Reranker
from rag_bbva.retrieval.retriever import Retriever

from .fakes_rag import CANDIDATOS, B, StoreFalso, ajustes, servicio


class StoreConHits(StoreFalso):
    def search(
        self, vector: Sequence[float], limit: int, *, section: str | None = None
    ) -> list[SearchHit]:
        return [
            SearchHit(id=f"p{i}", score=0.9, payload={"chunk_id": f"c{i}", "doc_id": f"d{i}",
                      "url": f"{B}/p{i}", "title": f"T{i}", "text": f"texto {i}"})
            for i in range(1, 4)
        ]  # fmt: skip


class RerankerFijo(Reranker):
    """Da al #1 el score indicado y a los demás uno menor."""

    name = "fijo"

    def __init__(self, score: float) -> None:
        self.score = score

    def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]:
        return [
            c.model_copy(update={"rerank_score": self.score - i, "rerank_rank": i + 1})
            for i, c in enumerate(candidates)
        ]


def _retriever(score: float, hard: float | None = -3.0) -> Retriever:
    return Retriever(
        embedder=FakeEmbedder(dimension=8), store=StoreConHits(), reranker=RerankerFijo(score),
        top_k=3, top_n=3, max_per_doc=2, min_score=1.6, hard_min_score=hard,
    )  # fmt: skip


# --- Retriever: tres zonas --------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "no_answer", "hard_no_answer", "gris"),
    [
        (4.0, False, False, False),  # por encima de RERANK_MIN_SCORE
        (1.6, False, False, False),  # justo en el umbral: responde
        (1.4, True, False, True),  # zona gris
        (-3.0, True, False, True),  # justo en el umbral duro: zona gris
        (-3.3, True, True, False),  # por debajo del umbral duro
    ],
)
def test_zonas_del_umbral(score: float, no_answer: bool, hard_no_answer: bool, gris: bool) -> None:
    r = _retriever(score).retrieve("pregunta")
    assert (r.no_answer, r.hard_no_answer, r.gray_zone) == (no_answer, hard_no_answer, gris)
    assert r.hard_min_score == -3.0


def test_sin_umbral_duro_se_comporta_como_m6() -> None:
    r = _retriever(1.4, hard=None).retrieve("pregunta")
    assert r.no_answer and r.hard_no_answer and not r.gray_zone


def test_sin_reranker_no_hay_umbrales() -> None:
    r = Retriever(
        embedder=FakeEmbedder(dimension=8), store=StoreConHits(), reranker=NoOpReranker(),
        min_score=1.6, hard_min_score=-3.0,
    ).retrieve("pregunta")  # fmt: skip
    assert not r.no_answer and not r.hard_no_answer and r.hard_min_score is None


def test_retrieval_result_sin_hard_hereda_no_answer() -> None:
    base = dict(query="q", section=None, reranker="x", top_k=1, results=[], candidates=[],
                top_score=None, min_score=1.6, retrieval_ms=0, rerank_ms=0)  # fmt: skip
    assert RetrievalResult(**base, no_answer=True).hard_no_answer is True  # type: ignore[arg-type]
    assert RetrievalResult(**base, no_answer=False).hard_no_answer is False  # type: ignore[arg-type]


def test_configuracion_rechaza_umbral_duro_mayor_que_el_blando() -> None:
    with pytest.raises(ValidationError, match="RERANK_HARD_MIN_SCORE"):
        Settings(_env_file=None, rerank_min_score=1.6, rerank_hard_min_score=2.0)  # type: ignore[call-arg]
    assert Settings(_env_file=None).rerank_hard_min_score == -3.0  # type: ignore[call-arg]


# --- Generador: llama o no al LLM y post-procesa la marca --------------------------------


def _recuperacion(no_answer: bool, hard: bool) -> RetrievalResult:
    return RetrievalResult(
        query="q", section=None, reranker="cross_encoder", top_k=20, results=list(CANDIDATOS),
        candidates=list(CANDIDATOS), top_score=1.0, min_score=1.6, no_answer=no_answer,
        retrieval_ms=1, rerank_ms=1, hard_min_score=-3.0, hard_no_answer=hard,
    )  # fmt: skip


def test_por_debajo_del_umbral_duro_no_llama_al_llm() -> None:
    llm = FakeLLMProvider()
    r = AnswerGenerator(llm).generate("receta de arepas", _recuperacion(True, True))
    assert llm.calls == [] and r.no_answer and not r.llm_called and r.text == NO_ANSWER_MESSAGE


def test_zona_gris_si_llama_al_llm_y_puede_responder() -> None:
    llm = FakeLLMProvider("El crédito de vivienda financia hasta el 70% [1].")
    r = AnswerGenerator(llm).generate("¿cuánto financian?", _recuperacion(True, False))
    assert len(llm.calls) == 1
    assert r.llm_called and r.gray_zone and not r.no_answer and not r.abstained
    assert r.text == "El crédito de vivienda financia hasta el 70% [1]."


def test_marca_de_abstencion_se_quita_y_marca_no_answer() -> None:
    llm = FakeLLMProvider(
        f"{ABSTENTION_MARKER} No encontré esa información en el sitio de Bancolombia. "
        "Puede consultar los créditos de vivienda [2]."
    )
    r = AnswerGenerator(llm).generate("¿y si soy independiente?", _recuperacion(True, False))
    assert r.no_answer and r.abstained and r.llm_called
    assert ABSTENTION_MARKER not in r.text
    assert r.text.startswith("No encontré esa información en el sitio de Bancolombia.")
    assert [s.url for s in r.sources] == [CANDIDATOS[1].url]  # la cita sigue siendo útil


def test_abstencion_tambien_por_encima_del_umbral() -> None:
    """Otra entidad (p. ej. Banco de Bogotá) puede tener score alto: el LLM se abstiene."""
    llm = FakeLLMProvider(f"{ABSTENTION_MARKER}Solo tengo información de Bancolombia.")
    r = AnswerGenerator(llm).generate("¿Banco de Bogotá?", _recuperacion(False, False))
    assert r.no_answer and r.abstained and not r.gray_zone
    assert r.text == "Solo tengo información de Bancolombia."


@pytest.mark.parametrize(
    ("texto", "esperado", "abstenida"),
    [
        ("Respuesta normal [1].", "Respuesta normal [1].", False),
        (f"{ABSTENTION_MARKER}", NO_ANSWER_MESSAGE, True),
        (f"  {ABSTENTION_MARKER}\n\nNo encontré.", "No encontré.", True),
        (f"No encontré. {ABSTENTION_MARKER}", "No encontré.", True),
    ],
)
def test_strip_abstention(texto: str, esperado: str, abstenida: bool) -> None:
    assert strip_abstention(texto) == (esperado, abstenida)


@pytest.mark.parametrize(
    ("trozos", "esperado"),
    [
        (["[SIN", "_INFO] No ", "encontré."], "No encontré."),
        (["[1] Un ", "CDT"], "[1] Un CDT"),
        (["Un CDT ", "es [2]."], "Un CDT es [2]."),
        (["[SIN_INFO]"], ""),
        (["[S"], "[S"),
    ],
)
def test_streaming_quita_la_marca(trozos: list[str], esperado: str) -> None:
    def fragmentos() -> Iterator[str]:
        yield from trozos

    assert "".join(_filtrar_marca(fragmentos())) == esperado


def test_streaming_con_abstencion_deja_respuesta_final_no_answer() -> None:
    llm = FakeLLMProvider(f"{ABSTENTION_MARKER} No encontré esa información.")
    fragmentos, final = AnswerGenerator(llm).stream("x", _recuperacion(True, False))
    assert "".join(fragmentos) == "No encontré esa información."
    assert final.answer is not None and final.answer.no_answer


def test_prompt_de_sistema_pide_la_marca() -> None:
    assert SYSTEM_PROMPT.count(ABSTENTION_MARKER) == 2  # regla 4 (sin contexto) y 5 (otra entidad)


# --- Servicio: la abstención se guarda como no_answer --------------------------------------


def test_servicio_guarda_la_abstencion_como_no_answer() -> None:
    from .fakes_rag import LLMGuionado, RetrieverFalso

    class LLMQueSeAbstiene(LLMGuionado):
        def _responder(self, mensajes):  # type: ignore[no-untyped-def]
            if self.es_reformulacion(mensajes):
                return super()._responder(mensajes)
            return f"{ABSTENTION_MARKER} No encontré esa información en el sitio de Bancolombia."

    repo = InMemoryConversationRepository()
    retriever = RetrieverFalso()
    rag = servicio(settings=ajustes(), repo=repo, llm=LLMQueSeAbstiene(), retriever=retriever)

    r = rag.ask(None, "¿Puedo pedirlo si soy independiente?")

    assert r.no_answer and r.model == "fake-model"
    assert ABSTENTION_MARKER not in r.answer
    assert repo.get_messages(r.conversation_id)[1].metrics.no_answer is True
