"""Retriever (M6): consulta → top-k por coseno → reranking → top-n diverso + umbral."""

import logging
import time
from dataclasses import dataclass

from rag_bbva.indexing.embedding import Embedder
from rag_bbva.indexing.vector_store import SearchHit, VectorStore
from rag_bbva.retrieval.models import Candidate, RetrievalResult
from rag_bbva.retrieval.reranker import NoOpReranker, Reranker, select_diverse

logger = logging.getLogger(__name__)


def _candidato(hit: SearchHit, posicion: int) -> Candidate:
    p = hit.payload
    return Candidate(
        id=hit.id,
        chunk_id=str(p.get("chunk_id", "")),
        doc_id=str(p.get("doc_id", "")),
        url=str(p.get("url", "")),
        title=p.get("title"),
        section=str(p.get("section", "")),
        heading_path=str(p.get("heading_path", "")),
        text=str(p.get("text", "")),
        cosine_score=hit.score,
        retrieval_rank=posicion,
    )


@dataclass
class Retriever:
    """Recupera el contexto de una pregunta.

    Umbral doble (ADR-016): por debajo de `hard_min_score` no se llama al LLM; entre
    `hard_min_score` y `min_score` (zona gris) sí, y el LLM decide si el contexto alcanza.

    El umbral `min_score` se aplica al score del reranker del #1: los scores coseno de
    e5 están comprimidos y no separan preguntas respondibles de las que no lo son
    (M04.md §8, M06.md §4). Sin reranker (`NoOpReranker`) no se aplica umbral.
    """

    embedder: Embedder
    store: VectorStore
    reranker: Reranker
    top_k: int = 20
    top_n: int = 5
    max_per_doc: int = 2
    min_score: float = 0.0
    hard_min_score: float | None = None  # None: sin zona gris (M6)

    def warm_up(self) -> None:
        """Carga los modelos perezosos (embedder y reranker) para que `retrieval_ms` y
        `rerank_ms` midan solo la consulta y no la carga inicial."""
        self.embedder.embed_query("calentamiento")
        self.reranker.warm_up()

    def retrieve(self, query: str, *, section: str | None = None) -> RetrievalResult:
        """Top-n de la consulta, con scores, tiempos y la marca de "sin información"."""
        inicio = time.perf_counter()
        vector = self.embedder.embed_query(query)
        hits = self.store.search(vector, self.top_k, section=section)
        candidatos = [_candidato(h, i) for i, h in enumerate(hits, 1)]
        t_retrieval = time.perf_counter()

        reordenados = self.reranker.rerank(query, candidatos)
        t_rerank = time.perf_counter()

        resultados = select_diverse(reordenados, self.top_n, self.max_per_doc)
        aplica_umbral = not isinstance(self.reranker, NoOpReranker)
        top_score = resultados[0].rerank_score if resultados else None
        sin_respuesta = not resultados or (
            aplica_umbral and (top_score is None or top_score < self.min_score)
        )
        if self.hard_min_score is None or not aplica_umbral:
            sin_respuesta_dura = sin_respuesta
        else:
            sin_respuesta_dura = not resultados or (
                top_score is None or top_score < self.hard_min_score
            )
        resultado = RetrievalResult(
            query=query,
            section=section,
            reranker=self.reranker.name,
            top_k=self.top_k,
            results=resultados,
            candidates=reordenados,
            top_score=top_score,
            min_score=self.min_score if aplica_umbral else None,
            no_answer=sin_respuesta,
            retrieval_ms=round((t_retrieval - inicio) * 1000, 1),
            rerank_ms=round((t_rerank - t_retrieval) * 1000, 1),
            hard_min_score=self.hard_min_score if aplica_umbral else None,
            hard_no_answer=sin_respuesta_dura,
        )
        logger.info(
            "Recuperación",
            extra={
                "candidatos": len(candidatos),
                "top_score": top_score,
                "sin_respuesta": sin_respuesta,
                "zona_gris": resultado.gray_zone,
                "retrieval_ms": resultado.retrieval_ms,
                "rerank_ms": resultado.rerank_ms,
            },
        )
        return resultado
