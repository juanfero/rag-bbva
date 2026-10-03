"""Modelos de la recuperación (M6): candidatos y resultado de una consulta."""

from typing import Any

from pydantic import BaseModel, model_validator


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
    # Umbral duro (ADR-016): por debajo no se llama al LLM. Si no se indica, coincide con
    # `no_answer` (comportamiento de M6, sin zona gris).
    hard_min_score: float | None = None
    hard_no_answer: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _hard_por_defecto(cls, datos: Any) -> Any:
        if isinstance(datos, dict) and datos.get("hard_no_answer") is None:
            datos = {**datos, "hard_no_answer": datos.get("no_answer", False)}
        return datos

    @property
    def gray_zone(self) -> bool:
        """Zona gris: no alcanza `RERANK_MIN_SCORE` pero sí el umbral duro; se le pide al
        LLM que responda o se abstenga con el contexto (ADR-016)."""
        return self.no_answer and not self.hard_no_answer and bool(self.results)
