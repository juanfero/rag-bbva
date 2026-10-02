"""Embeddings (M4): interfaz `Embedder` y sus implementaciones.

- `SentenceTransformerEmbedder`: modelo e5 en CPU. e5 requiere los prefijos
  `query: ` (preguntas) y `passage: ` (fragmentos); los vectores salen con
  normalización L2, así que el producto punto es la similitud coseno.
- `FakeEmbedder`: determinista y sin modelo, para tests (bolsa de palabras con hash).
"""

import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from rag_bbva.exceptions import IndexingError

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

PREFIJO_CONSULTA = "query: "
PREFIJO_PASAJE = "passage: "


class Embedder(ABC):
    """Convierte textos en vectores normalizados (L2)."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensión de los vectores."""

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        """Vectores de fragmentos a indexar, forma (n, dimension)."""

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray:
        """Vector de una pregunta, forma (dimension,)."""

    @property
    @abstractmethod
    def max_tokens(self) -> int:
        """Máximo de tokens que el modelo procesa por texto (el resto se trunca)."""

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Tokens que ocupa `text` como pasaje (con prefijo y tokens especiales)."""


class SentenceTransformerEmbedder(Embedder):
    """Embedder sobre sentence-transformers, en CPU y con caché de modelos local.

    El modelo se carga de forma perezosa (la primera vez que se usa) y se reutiliza.
    """

    def __init__(self, model_name: str, cache_dir: Path, batch_size: int = 32) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self._modelo: SentenceTransformer | None = None

    @property
    def model(self) -> "SentenceTransformer":
        """Modelo cargado (perezoso)."""
        if self._modelo is None:
            from sentence_transformers import SentenceTransformer

            inicio = time.perf_counter()
            try:
                self._modelo = SentenceTransformer(
                    self.model_name, cache_folder=str(self.cache_dir), device="cpu"
                )
            except OSError as exc:
                raise IndexingError(
                    "No se pudo cargar el modelo de embeddings",
                    detail=f"{self.model_name} en {self.cache_dir}: {exc}",
                ) from exc
            logger.info(
                "Modelo de embeddings cargado",
                extra={
                    "modelo": self.model_name,
                    "segundos": round(time.perf_counter() - inicio, 1),
                },
            )
        return self._modelo

    @property
    def dimension(self) -> int:
        modelo = self.model
        # sentence-transformers ≥ 5 renombró el método; se aceptan ambas versiones (≥ 3).
        obtener = getattr(modelo, "get_embedding_dimension", None) or (
            modelo.get_sentence_embedding_dimension
        )
        dimension = obtener()
        if dimension is None:
            raise IndexingError("El modelo no informa su dimensión", detail=self.model_name)
        return int(dimension)

    @property
    def max_tokens(self) -> int:
        return int(self.model.max_seq_length)

    def count_tokens(self, text: str) -> int:
        return len(self.model.tokenizer(PREFIJO_PASAJE + text)["input_ids"])

    def _encode(self, texts: list[str]) -> np.ndarray:
        vectores = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectores, dtype=np.float32)

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode([PREFIJO_PASAJE + t for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._encode([PREFIJO_CONSULTA + text])[0]


_TOKEN = re.compile(r"\w+")


class FakeEmbedder(Embedder):
    """Embedder determinista para tests: cada palabra suma 1 en una dimensión elegida
    por hash, y el vector se normaliza. Textos con palabras en común quedan cerca."""

    def __init__(self, dimension: int = 384, max_tokens: int = 512) -> None:
        self._dimension = dimension
        self._max_tokens = max_tokens

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def count_tokens(self, text: str) -> int:
        return len(_TOKEN.findall(PREFIJO_PASAJE + text)) + 2

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self._dimension, dtype=np.float32)
        for palabra in _TOKEN.findall(text.lower()):
            indice = int(hashlib.md5(palabra.encode("utf-8")).hexdigest(), 16) % self._dimension
            vector[indice] += 1.0
        norma = np.linalg.norm(vector)
        return vector / norma if norma else vector

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dimension), dtype=np.float32)
        return np.stack([self._vector(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)
