"""Pruebas de la recuperación, el reranking y la calibración (M6, sin red ni modelos).

Las del cross-encoder real están marcadas `slow` y se saltan si no está en la caché.
"""

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient

from rag_bbva.config import Settings, get_settings
from rag_bbva.indexing.embedding import FakeEmbedder, is_model_cached
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.indexing.vector_store import QdrantVectorStore, VectorPoint, point_id
from rag_bbva.retrieval.calibration import best_threshold, evaluate, load_questions
from rag_bbva.retrieval.models import Candidate
from rag_bbva.retrieval.reranker import (
    CrossEncoderReranker,
    NoOpReranker,
    Reranker,
    passage_for,
    select_diverse,
)
from rag_bbva.retrieval.retriever import Retriever

B = "https://www.bancolombia.com"


def _cand(doc: str, rank: int, rerank: float | None = None, texto: str = "t") -> Candidate:
    return Candidate(
        id=f"{doc}-{rank}", chunk_id=f"{doc}-{rank}", doc_id=doc, url=f"{B}/{doc}",
        title=doc.title(), section="personas", heading_path=doc.title(), text=texto,
        cosine_score=1 - rank / 100, retrieval_rank=rank, rerank_score=rerank,
    )  # fmt: skip


class RerankerPorPalabra(Reranker):
    """Doble determinista: score = veces que aparece la palabra clave en el texto."""

    name = "por_palabra"

    def __init__(self, palabra: str) -> None:
        self.palabra = palabra

    def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]:
        con = [
            c.model_copy(update={"rerank_score": float(c.text.lower().count(self.palabra))})
            for c in candidates
        ]
        orden = sorted(con, key=lambda c: (-(c.rerank_score or 0), c.retrieval_rank))
        return [c.model_copy(update={"rerank_rank": i}) for i, c in enumerate(orden, 1)]


# ---------------------------------------------------------------- NoOp y diversidad


def test_noop_conserva_el_orden_y_no_asigna_score() -> None:
    candidatos = [_cand("a", 1), _cand("b", 2), _cand("c", 3)]

    resultado = NoOpReranker().rerank("q", candidatos)

    assert [c.id for c in resultado] == ["a-1", "b-2", "c-3"]
    assert [c.rerank_rank for c in resultado] == [1, 2, 3]
    assert all(c.rerank_score is None for c in resultado)


def test_select_diverse_limita_chunks_por_documento() -> None:
    candidatos = [_cand("faq", 1), _cand("faq", 2), _cand("faq", 3), _cand("otra", 4)]

    elegidos = select_diverse(candidatos, top_n=3, max_per_doc=2)

    assert [c.id for c in elegidos] == ["faq-1", "faq-2", "otra-4"]


def test_select_diverse_rellena_si_no_alcanza_y_sin_limite() -> None:
    candidatos = [_cand("faq", i) for i in range(1, 5)]

    assert [c.id for c in select_diverse(candidatos, 3, 2)] == ["faq-1", "faq-2", "faq-3"]
    assert len(select_diverse(candidatos, 4, 0)) == 4  # 0 = sin límite
    assert select_diverse(candidatos[:2], 5, 2) == candidatos[:2]


def test_passage_for_incluye_titulo_y_ruta() -> None:
    texto = passage_for(_cand("cajeros", 1, texto="Te acompañamos."))

    assert texto == "Cajeros\nCajeros\n\nTe acompañamos."


def test_cross_encoder_carga_perezosa() -> None:
    reranker = CrossEncoderReranker("org/modelo", Path("no-existe"))

    assert reranker._modelo is None
    assert reranker.rerank("q", []) == []  # sin candidatos no carga el modelo
    assert reranker._modelo is None


# ---------------------------------------------------------------- Retriever


TEXTOS = {
    "tarjetas": "Tarjeta de crédito: bloquea tu tarjeta desde la app. Tarjeta perdida.",
    "cdt": "Un CDT es un certificado de depósito a término para invertir tu plata.",
    "cajeros": "Cajeros automáticos en todo el país para retirar plata.",
    "leasing": "Leasing de vehículos para empresas.",
}


@pytest.fixture
def store() -> QdrantVectorStore:
    embedder = FakeEmbedder(dimension=64)
    almacen = QdrantVectorStore(QdrantClient(":memory:"), "docs")
    almacen.ensure_collection(64)
    puntos = []
    for slug, texto in TEXTOS.items():
        for i in range(3):  # 3 chunks por documento
            chunk_id = f"{slug}-{i}"
            puntos.append(
                VectorPoint(
                    point_id(chunk_id),
                    embedder.embed_documents([f"{texto} parte {i}"])[0],
                    {
                        "chunk_id": chunk_id,
                        "doc_id": slug,
                        "url": f"{B}/{slug}",
                        "title": slug.title(),
                        "heading_path": slug.title(),
                        "text": f"{texto} parte {i}",
                        "section": "negocios" if slug == "leasing" else "personas",
                    },
                )
            )
    almacen.upsert(puntos)
    return almacen


def _retriever(store: QdrantVectorStore, reranker: Reranker, **kw: float) -> Retriever:
    return Retriever(
        embedder=FakeEmbedder(dimension=64), store=store, reranker=reranker,
        top_k=int(kw.get("top_k", 10)), top_n=int(kw.get("top_n", 3)),
        max_per_doc=int(kw.get("max_per_doc", 2)), min_score=kw.get("min_score", 1.0),
    )  # fmt: skip


def test_retriever_reordena_aplica_diversidad_y_registra_tiempos(store: QdrantVectorStore) -> None:
    retriever = _retriever(store, RerankerPorPalabra("tarjeta"))

    resultado = retriever.retrieve("¿cómo bloqueo mi tarjeta de crédito?")

    assert resultado.results[0].doc_id == "tarjetas"
    assert sum(c.doc_id == "tarjetas" for c in resultado.results) == 2  # max_per_doc
    assert len(resultado.results) == 3
    assert len(resultado.candidates) == 10
    assert resultado.top_score == resultado.results[0].rerank_score
    assert not resultado.no_answer
    assert resultado.retrieval_ms >= 0 and resultado.rerank_ms >= 0
    assert resultado.reranker == "por_palabra"
    assert all(c.retrieval_rank >= 1 and c.rerank_rank for c in resultado.results)


def test_umbral_marca_sin_respuesta(store: QdrantVectorStore) -> None:
    """Si el #1 del reranker no alcanza `min_score`, `no_answer` es verdadero."""
    retriever = _retriever(store, RerankerPorPalabra("fútbol"), min_score=1.0)

    resultado = retriever.retrieve("¿quién ganó el mundial de fútbol?")

    assert resultado.top_score == 0.0
    assert resultado.no_answer
    assert resultado.min_score == 1.0


def test_sin_reranker_no_se_aplica_umbral(store: QdrantVectorStore) -> None:
    resultado = _retriever(store, NoOpReranker(), min_score=99).retrieve("cdt plata")

    assert resultado.min_score is None
    assert resultado.top_score is None
    assert not resultado.no_answer
    assert [c.retrieval_rank for c in resultado.results] == sorted(
        c.retrieval_rank for c in resultado.results
    )


def test_filtro_por_seccion_y_coleccion_vacia(store: QdrantVectorStore) -> None:
    resultado = _retriever(store, RerankerPorPalabra("leasing")).retrieve(
        "leasing", section="negocios"
    )
    vacio = QdrantVectorStore(QdrantClient(":memory:"), "vacia")
    vacio.ensure_collection(64)
    sin_nada = _retriever(vacio, RerankerPorPalabra("x")).retrieve("hola")

    assert {c.section for c in resultado.results} == {"negocios"}
    assert sin_nada.results == [] and sin_nada.no_answer and sin_nada.top_score is None


# ---------------------------------------------------------------- fábrica


def test_factory_respeta_el_flag_del_reranker(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("RERANKER_ENABLED", "false")
    fabrica = ComponentFactory(Settings(_env_file=None))
    assert isinstance(fabrica.create_reranker(), NoOpReranker)
    assert isinstance(fabrica.create_reranker(enabled=True), CrossEncoderReranker)

    clean_env.setenv("RERANKER_ENABLED", "true")
    clean_env.setenv("RERANK_TOP_N", "4")
    clean_env.setenv("RERANK_MAX_CHUNKS_PER_DOC", "1")
    clean_env.setenv("RERANK_MIN_SCORE", "2.5")
    clean_env.setenv("QDRANT_URL", ":memory:")
    clean_env.setenv("EMBEDDING_PROVIDER", "fake")
    retriever = ComponentFactory(Settings(_env_file=None)).create_retriever()

    assert isinstance(retriever.reranker, CrossEncoderReranker)
    assert retriever.reranker._modelo is None  # perezoso
    assert (retriever.top_k, retriever.top_n, retriever.max_per_doc, retriever.min_score) == (
        20, 4, 1, 2.5,
    )  # fmt: skip
    assert ComponentFactory(Settings(_env_file=None)).create_retriever(top_n=2).top_n == 2


# ---------------------------------------------------------------- calibración


def test_best_threshold_con_separacion_perfecta() -> None:
    elegido = best_threshold([5.0, 7.0, 9.0], [-3.0, -1.0, 1.0])

    assert elegido.threshold == 3.0  # punto medio del hueco más ancho (1 → 5)
    assert (elegido.true_positive, elegido.true_negative, elegido.accuracy) == (3, 3, 1.0)
    assert elegido.margin == 4.0


def test_best_threshold_con_solapamiento_y_matriz() -> None:
    elegido = best_threshold([4.0, 2.0, -2.5], [5.3, 1.4, -4.0])

    assert elegido.accuracy == pytest.approx(4 / 6, abs=1e-4)
    matriz = evaluate(1.6, [4.0, 2.0, -2.5], [5.3, 1.4, -4.0])
    assert (matriz.true_positive, matriz.false_negative) == (2, 1)
    assert (matriz.true_negative, matriz.false_positive) == (2, 1)


def test_archivo_de_calibracion_del_repo() -> None:
    preguntas = load_questions(Path("eval/calibration.jsonl"))

    respondibles = [p for p in preguntas if p.answerable]
    assert len(respondibles) == 15 and len(preguntas) == 30
    assert all(p.expected_url and p.expected_url.startswith(B) for p in respondibles)
    categorias = {p.category for p in preguntas if not p.answerable}
    assert {"fuera_de_dominio", "otro_banco", "prensa_L06", "simulador_L09"} <= categorias
    assert len({p.id for p in preguntas}) == 30
    json.dumps([p.model_dump() for p in preguntas])


# ---------------------------------------------------------------- cross-encoder real (slow)


@pytest.fixture(scope="module")
def cross_encoder() -> CrossEncoderReranker:
    ajustes = get_settings()
    if not is_model_cached(ajustes.reranker_model, ajustes.model_cache_dir):
        pytest.skip(
            f"El reranker {ajustes.reranker_model} no está en la caché local "
            f"(MODEL_CACHE_DIR={ajustes.model_cache_dir}); se omite para no descargarlo."
        )
    return CrossEncoderReranker(ajustes.reranker_model, ajustes.model_cache_dir)


@pytest.mark.slow
def test_cross_encoder_real_prefiere_el_pasaje_relevante(
    cross_encoder: CrossEncoderReranker,
) -> None:
    candidatos = [
        _cand("futbol", 1, texto="El partido de fútbol terminó 2 a 1."),
        _cand("tarjetas", 2, texto="Las tarjetas de crédito permiten comprar en cuotas."),
        _cand(
            "cdt",
            3,
            texto="Un CDT es un certificado de depósito a término: inviertes a plazo fijo.",
        ),
    ]

    resultado = cross_encoder.rerank("¿qué es un CDT?", candidatos)

    assert resultado[0].doc_id == "cdt"
    assert resultado[0].rerank_score is not None and resultado[0].rerank_score > 1.6
    assert resultado[-1].rerank_score is not None and resultado[-1].rerank_score < 1.6
    assert [c.rerank_rank for c in resultado] == [1, 2, 3]
    assert np.isfinite([c.rerank_score for c in resultado]).all()
