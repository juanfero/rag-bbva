"""Pruebas del servicio RAG (Facade, M9) con dobles: sin red ni modelos."""

import pytest

from rag_bbva.exceptions import ConversationNotFoundError, IndexingError, LLMError
from rag_bbva.memory.repository import InMemoryConversationRepository
from rag_bbva.services.rag_service import RAGService

from .fakes_rag import REESCRITA, B, LLMGuionado, RetrieverFalso, ajustes, servicio


def _armar(
    **cambios: object,
) -> tuple[RAGService, InMemoryConversationRepository, LLMGuionado, RetrieverFalso]:
    repo, llm, retriever = InMemoryConversationRepository(), LLMGuionado(), RetrieverFalso()
    rag = servicio(settings=ajustes(**cambios), repo=repo, llm=llm, retriever=retriever)
    return rag, repo, llm, retriever


def test_primer_turno_crea_conversacion_y_persiste_pregunta_y_respuesta() -> None:
    rag, repo, llm, retriever = _armar()

    r = rag.ask(None, "¿Qué es el crédito de vivienda?")

    assert r.answer == "Necesita ser mayor de edad [1] y tener ingresos demostrables [2]."
    assert [(s.n, s.url, s.title) for s in r.sources] == [
        (1, f"{B}/personas/creditos/vivienda/requisitos", "Requisitos"),
        (2, f"{B}/personas/creditos/vivienda", "Vivienda"),
    ]
    assert r.rewritten_query is None  # history_only: la primera pregunta va tal cual
    assert retriever.consultas == ["¿Qué es el crédito de vivienda?"]
    assert len(llm.llamadas_de_reformulacion()) == 0
    assert r.no_answer is False and r.model == "fake-model"
    assert r.timings.retrieval == 12.0 and r.timings.rerank == 300.0
    assert r.timings.total >= r.timings.rewrite
    assert r.tokens.total == r.tokens.prompt + r.tokens.completion > 0

    mensajes = repo.get_messages(r.conversation_id)
    assert [(m.id, m.role) for m in mensajes] == [
        (r.question_message_id, "user"),
        (r.message_id, "assistant"),
    ]
    respuesta = mensajes[1]
    assert respuesta.sources == [s.model_dump() for s in r.sources]
    assert respuesta.metrics.top_score == 4.0
    assert respuesta.metrics.no_answer is False
    assert respuesta.metrics.retrieval_ms == 12.0
    assert respuesta.metrics.prompt_tokens == r.tokens.prompt
    assert repo.require_conversation(r.conversation_id).title == "¿Qué es el crédito de vivienda?"


def test_memoria_la_pregunta_dependiente_se_reformula_con_el_historial() -> None:
    """Prueba de memoria del plan: "¿y cuáles son los requisitos?" llega reformulada."""
    rag, _repo, llm, retriever = _armar()
    primero = rag.ask(None, "¿Qué es el crédito de vivienda?")

    segundo = rag.ask(primero.conversation_id, "¿y cuáles son los requisitos?")

    (reformulacion,) = llm.llamadas_de_reformulacion()
    prompt = reformulacion[1]["content"]
    assert "Usuario: ¿Qué es el crédito de vivienda?" in prompt
    assert f"Asistente: {primero.answer}" in prompt
    assert prompt.endswith("Pregunta: ¿y cuáles son los requisitos?")
    assert segundo.rewritten_query == REESCRITA
    assert retriever.consultas[-1] == REESCRITA  # se recupera con la reformulada
    respuesta = llm.llamadas_de_respuesta()[-1][1]["content"]
    assert "Pregunta: ¿y cuáles son los requisitos?" in respuesta
    assert respuesta.endswith(f"conversación): {REESCRITA}")
    assert segundo.conversation_id == primero.conversation_id


def test_historial_respeta_history_window_n() -> None:
    rag, _repo, llm, _ = _armar(history_window_n=2)
    cid = rag.ask(None, "pregunta uno").conversation_id
    rag.ask(cid, "pregunta dos")
    rag.ask(cid, "pregunta tres")

    ultimo = llm.llamadas_de_reformulacion()[-1][1]["content"]
    assert "pregunta uno" not in ultimo
    assert "Usuario: pregunta dos" in ultimo
    assert ultimo.count("Asistente:") == 1


def test_history_window_cero_desactiva_la_memoria() -> None:
    rag, _repo, llm, retriever = _armar(history_window_n=0)
    cid = rag.ask(None, "¿Qué es el crédito de vivienda?").conversation_id

    r = rag.ask(cid, "¿y cuáles son los requisitos?")

    assert llm.llamadas_de_reformulacion() == []
    assert r.rewritten_query is None
    assert retriever.consultas[-1] == "¿y cuáles son los requisitos?"


def test_no_answer_no_llama_al_llm_y_guarda_el_turno() -> None:
    rag, repo, llm, retriever = _armar()
    retriever.no_answer = True

    r = rag.ask(None, "receta de arepas")

    assert r.no_answer is True and r.sources == [] and r.model is None
    assert "No encontré información suficiente" in r.answer
    assert llm.calls == []
    assert r.tokens.total == 0 and r.timings.llm == 0
    assert repo.get_messages(r.conversation_id)[1].metrics.no_answer is True


def test_turno_atomico_si_falla_el_llm_no_se_guarda_nada() -> None:
    rag, repo, llm, _ = _armar()
    cid = rag.ask(None, "¿Qué es el crédito de vivienda?").conversation_id
    llm.falla_respuesta = True

    with pytest.raises(LLMError, match="cupo"):
        rag.ask(cid, "¿y cuáles son los requisitos?")
    with pytest.raises(LLMError):
        rag.ask(None, "pregunta de una conversación nueva")

    assert len(repo.get_messages(cid)) == 2  # sin pregunta suelta
    assert len(repo.list_conversations()) == 1  # sin conversación vacía

    llm.falla_respuesta = False
    siguiente = rag.ask(cid, "¿y cuáles son los requisitos?")
    contexto = llm.llamadas_de_reformulacion()[-1][1]["content"]
    assert contexto.count("Usuario:") == 1  # la pregunta fallida no contamina el contexto
    assert len(repo.get_messages(siguiente.conversation_id)) == 4


def test_si_falla_qdrant_no_se_guarda_nada() -> None:
    rag, repo, _llm, retriever = _armar()
    retriever.store.caido = True  # type: ignore[attr-defined]

    with pytest.raises(IndexingError):
        rag.ask(None, "¿Qué es un CDT?")
    assert repo.list_conversations() == []


def test_conversacion_inexistente_falla_antes_de_gastar_tokens() -> None:
    rag, repo, llm, retriever = _armar()

    with pytest.raises(ConversationNotFoundError):
        rag.ask("no-existe", "¿y los requisitos?")

    assert llm.calls == [] and retriever.consultas == []
    assert repo.list_conversations() == []


def test_modo_always_reformula_tambien_la_primera_pregunta() -> None:
    rag, _repo, llm, retriever = _armar(query_rewrite_mode="always")
    r = rag.ask(None, "¿cómo pago el 4 por mil?")
    assert r.rewritten_query == REESCRITA
    assert retriever.consultas == [REESCRITA]
    assert "(sin historial)" in llm.llamadas_de_reformulacion()[0][1]["content"]


def test_warm_up_calienta_el_retriever() -> None:
    rag, _repo, _llm, retriever = _armar()
    rag.warm_up()
    assert retriever.calentado
