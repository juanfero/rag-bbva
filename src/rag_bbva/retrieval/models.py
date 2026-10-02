"""Modelos de la recuperación (M6): candidatos y resultado de una consulta."""

from pydantic import BaseModel


class Candidate(BaseModel):
    """Chunk recuperado para una consulta, con sus scores y posiciones."""

    id: str  # id del punto en el VectorStore
    chunk_id: str
    doc_id: str
    url: str
    title: str | None
    section: str
    heading_path: str
    text: str
    cosine_score: float
    retrieval_rank: int  # posición en el top-k por coseno (desde 1)
    rerank_score: float | None = None  # score del reranker (logit del cross-encoder)
    rerank_rank: int | None = None  # posición tras el reranking (desde 1)


class RetrievalResult(BaseModel):
    """Resultado de `Retriever.retrieve`: lo que usan el LLM (M7) y la analítica (M11)."""

    query: str
    section: str | None
    reranker: str
    top_k: int
    results: list[Candidate]  # top-n final (diverso por documento) que va al LLM
    candidates: list[Candidate]  # los top-k recuperados, ya con score del reranker
    top_score: float | None  # score del reranker del #1 (None sin reranker)
    min_score: float | None  # umbral aplicado (None si no se aplica)
    no_answer: bool  # el #1 no alcanza el umbral: "sin información suficiente"
    retrieval_ms: float
    rerank_ms: float
