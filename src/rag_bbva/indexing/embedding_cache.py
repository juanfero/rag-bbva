"""Caché de embeddings en disco, por hash del texto embebido (M5).

Un archivo `.npz` por modelo en `EMBEDDINGS_CACHE_DIR` guarda pares (hash del texto,
vector). Re-ingestar sin cambios, o reconstruir la colección con `--recreate`, reutiliza
los vectores sin volver a pasar por el modelo.
"""

import hashlib
import logging
import os
import re
from pathlib import Path

import numpy as np

from rag_bbva.exceptions import IndexingError

logger = logging.getLogger(__name__)


def text_hash(texto: str) -> str:
    """SHA-256 del texto que se embebe (el `embedding_text` del chunk)."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Vectores ya calculados por un modelo, indexados por `text_hash`."""

    def __init__(self, cache_dir: Path, model_name: str) -> None:
        self.cache_dir = cache_dir
        self.model_name = model_name
        nombre = re.sub(r"[^A-Za-z0-9_.-]+", "--", model_name)
        self.path = cache_dir / f"{nombre}.npz"
        self._vectores: dict[str, np.ndarray] | None = None
        self._modificada = False

    def _cargar(self) -> dict[str, np.ndarray]:
        if self._vectores is None:
            self._vectores = {}
            if self.path.exists():
                try:
                    with np.load(self.path) as datos:
                        claves, vectores = datos["keys"], datos["vectors"]
                except (OSError, ValueError, KeyError) as exc:
                    logger.warning(
                        "Caché de embeddings ilegible; se ignora",
                        extra={"ruta": str(self.path), "error": repr(exc)},
                    )
                else:
                    self._vectores = {str(c): v for c, v in zip(claves, vectores, strict=True)}
        return self._vectores

    @property
    def dimension(self) -> int | None:
        """Dimensión de los vectores guardados, o `None` si la caché está vacía."""
        vectores = self._cargar()
        return int(next(iter(vectores.values())).shape[0]) if vectores else None

    def __len__(self) -> int:
        return len(self._cargar())

    def get(self, key: str) -> np.ndarray | None:
        """Vector guardado para `key`, o `None`."""
        return self._cargar().get(key)

    def put(self, key: str, vector: np.ndarray) -> None:
        """Guarda (en memoria) el vector de `key`; `save()` lo persiste."""
        self._cargar()[key] = np.asarray(vector, dtype=np.float32)
        self._modificada = True

    def save(self) -> None:
        """Escribe la caché de forma atómica si cambió."""
        if not self._modificada:
            return
        vectores = self._cargar()
        claves = sorted(vectores)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporal = self.path.with_name(f".{self.path.stem}.tmp.npz")
            np.savez(
                temporal,
                keys=np.array(claves),
                vectors=np.stack([vectores[c] for c in claves]),
            )
            os.replace(temporal, self.path)
        except OSError as exc:
            raise IndexingError(
                "No se pudo guardar la caché de embeddings", detail=str(exc)
            ) from exc
        self._modificada = False
