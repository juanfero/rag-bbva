"""Reranking (patrón Strategy, bonus del caso).

- `CrossEncoderReranker`: cross-encoder multilingüe que lee pregunta y fragmento juntos
  y devuelve un score de relevancia (logit, sin acotar). Es más preciso que el coseno
  de los embeddings, que compara vectores calculados por separado.
- `NoOpReranker`: conserva el orden del retrieval (para `RERANKER_ENABLED=false`).

`select_diverse` limita cuántos chunks de un mismo documento llegan al top-n.
"""

import logging
import time
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from rag_bbva.exceptions import RetrievalError
from rag_bbva.indexing.embedding import is_model_cached
from rag_bbva.retrieval.models import Candidate

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)


def passage_for(candidate: Candidate) -> str:
    """Texto que lee el reranker: título y ruta de títulos del chunk, y su contenido."""
    cabecera = "\n".join(p for p in (candidate.title, candidate.heading_path) if p)
    return f"{cabecera}\n\n{candidate.text}" if cabecera else candidate.text


class Reranker(ABC):
    """Reordena los candidatos de una consulta."""

    name: str

    @abstractmethod
    def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]:
        """Candidatos ordenados por relevancia, con `rerank_score` y `rerank_rank`."""

    def warm_up(self) -> None:  # noqa: B027 - gancho opcional, vacío por defecto
        """Carga lo que haga falta antes de medir latencias (por defecto, nada)."""


class NoOpReranker(Reranker):
    """Sin reranking: conserva el orden del retrieval y no asigna score."""

    name = "noop"

    def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]:
        return [c.model_copy(update={"rerank_rank": i}) for i, c in enumerate(candidates, 1)]


class CrossEncoderReranker(Reranker):
    """Cross-encoder en CPU, cargado de forma perezosa desde la caché de modelos."""

    name = "cross_encoder"

    def __init__(
        self, model_name: str, cache_dir: Path, *, max_length: int = 512, batch_size: int = 16
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.max_length = max_length
        self.batch_size = batch_size
        self._modelo: CrossEncoder | None = None

    @property
    def model(self) -> "CrossEncoder":
        """Modelo cargado (perezoso). Con el modelo en caché no se consulta el Hub."""
        if self._modelo is None:
            from sentence_transformers import CrossEncoder

            inicio = time.perf_counter()
            try:
                self._modelo = CrossEncoder(
                    self.model_name,
                    cache_folder=str(self.cache_dir),
                    device="cpu",
                    max_length=self.max_length,
                    local_files_only=is_model_cached(self.model_name, self.cache_dir),
                )
            except OSError as exc:
                raise RetrievalError(
                    "No se pudo cargar el modelo de reranking",
                    detail=f"{self.model_name} en {self.cache_dir}: {exc}",
                ) from exc
            logger.info(
                "Reranker cargado",
                extra={
                    "modelo": self.model_name,
                    "segundos": round(time.perf_counter() - inicio, 1),
                },
            )
        return self._modelo

    def warm_up(self) -> None:
        """Carga el modelo (la primera carga no debe contar como latencia de rerank)."""
        _ = self.model

    def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]:
        if not candidates:
            return []
        scores = self.model.predict(
            [(query, passage_for(c)) for c in candidates],
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        con_score = [
            c.model_copy(update={"rerank_score": float(s)})
            for c, s in zip(candidates, scores, strict=True)
        ]
        # Orden estable: a igual score, manda la posición del retrieval.
        ordenados = sorted(con_score, key=lambda c: (-(c.rerank_score or 0.0), c.retrieval_rank))
        return [c.model_copy(update={"rerank_rank": i}) for i, c in enumerate(ordenados, 1)]


def select_diverse(
    candidates: Sequence[Candidate], top_n: int, max_per_doc: int
) -> list[Candidate]:
    """Los primeros `top_n` con como mucho `max_per_doc` chunks por documento.

    Si con el límite no se completa el top-n (pocos documentos distintos), se rellena
    con los candidatos omitidos, en orden. `max_per_doc=0` desactiva el límite.
    """
    if max_per_doc <= 0:
        return list(candidates[:top_n])
    elegidos: list[Candidate] = []
    omitidos: list[Candidate] = []
    por_doc: Counter[str] = Counter()
    for candidato in candidates:
        if len(elegidos) == top_n:
            break
        if por_doc[candidato.doc_id] < max_per_doc:
            elegidos.append(candidato)
            por_doc[candidato.doc_id] += 1
        else:
            omitidos.append(candidato)
    faltan = top_n - len(elegidos)
    if faltan > 0:
        elegidos += omitidos[:faltan]
    return elegidos
