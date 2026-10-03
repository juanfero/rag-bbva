"""Almacén vectorial (M5): interfaz `VectorStore` y su adaptador para Qdrant.

`VectorStore` es la frontera entre el dominio (chunks, vectores, filtros por sección) y
la base vectorial. `QdrantVectorStore` adapta `qdrant-client` a esa interfaz y traduce
sus errores a `IndexingError`, de modo que la ingesta y la recuperación (M6) no
dependen de Qdrant ni de sus excepciones.
"""

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
import numpy as np
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from rag_bbva.exceptions import IndexingError

logger = logging.getLogger(__name__)

# Espacio de nombres fijo para derivar ids de punto (UUID) a partir de `chunk_id`.
NAMESPACE_CHUNKS = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/juanfero/rag-bbva/chunks")
MEMORIA = ":memory:"
CAMPO_SECCION = "section"
CAMPO_HASH = "text_hash"
_LOTE_SCROLL = 1000

T = TypeVar("T")


def point_id(chunk_id: str) -> str:
    """Id de punto determinista (UUID v5) para un `chunk_id`: re-ingestar no duplica."""
    return str(uuid.uuid5(NAMESPACE_CHUNKS, chunk_id))


@dataclass(frozen=True)
class VectorPoint:
    """Punto a guardar: id, vector y payload (texto y metadatos del chunk)."""

    id: str
    vector: np.ndarray
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    """Resultado de una búsqueda."""

    id: str
    score: float
    payload: dict[str, Any]


class VectorStore(ABC):
    """Operaciones que la aplicación necesita de una base vectorial."""

    @abstractmethod
    def ensure_collection(self, dimension: int, *, recreate: bool = False) -> None:
        """Crea la colección (coseno) si no existe; con `recreate` la borra antes."""

    @abstractmethod
    def upsert(self, points: Sequence[VectorPoint]) -> None:
        """Inserta o reemplaza puntos (por lotes)."""

    @abstractmethod
    def search(
        self, vector: np.ndarray, top_k: int, *, section: str | None = None
    ) -> list[SearchHit]:
        """Los `top_k` puntos más similares, opcionalmente solo de una sección."""

    @abstractmethod
    def count(self) -> int:
        """Número de puntos de la colección."""

    @abstractmethod
    def delete(self, ids: Sequence[str]) -> None:
        """Borra puntos por id."""

    @abstractmethod
    def stored_hashes(self) -> dict[str, str]:
        """{id de punto: hash del texto embebido} de todos los puntos guardados."""


class QdrantVectorStore(VectorStore):
    """Adaptador de `qdrant-client` (servidor o `:memory:` en tests)."""

    def __init__(
        self, client: QdrantClient, collection: str, *, batch_size: int = 128, url: str = ""
    ) -> None:
        self.client = client
        self.collection = collection
        self.batch_size = batch_size
        self.url = url or "Qdrant"

    @classmethod
    def from_url(
        cls, url: str, collection: str, *, timeout: float = 10, batch_size: int = 128
    ) -> "QdrantVectorStore":
        """Cliente contra un servidor Qdrant; no conecta hasta la primera operación.

        `url=":memory:"` usa el modo local en memoria de `qdrant-client` (tests, demos).
        """
        if url == MEMORIA:
            return cls(QdrantClient(location=MEMORIA), collection, batch_size=batch_size, url=url)
        # Sin verificación de compatibilidad al crear el cliente: haría una petición al
        # servidor de inmediato. La compatibilidad se fija por versión (imagen 1.19.1 en
        # docker-compose.yml y qdrant-client 1.19.x en pyproject.toml).
        cliente = QdrantClient(url=url, timeout=int(timeout), check_compatibility=False)
        return cls(cliente, collection, batch_size=batch_size, url=url)

    def _llamar(self, operacion: str, funcion: Callable[[], T]) -> T:
        """Ejecuta una llamada a Qdrant traduciendo sus errores a `IndexingError`."""
        try:
            return funcion()
        except (ResponseHandlingException, httpx.HTTPError, ConnectionError) as exc:
            raise IndexingError(
                f"No se pudo conectar con Qdrant en {self.url}. ¿Está levantado? "
                "Pruebe `docker compose up -d qdrant` y revise QDRANT_URL",
                detail=f"{operacion}: {exc}",
            ) from exc
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                raise self._coleccion_inexistente(operacion, exc) from exc
            raise IndexingError(
                f"Qdrant rechazó la operación '{operacion}'", detail=str(exc)
            ) from exc
        except ValueError as exc:
            # El modo local (`:memory:`) de qdrant-client lanza ValueError en lugar de 404.
            if "not found" not in str(exc).lower():
                raise
            raise self._coleccion_inexistente(operacion, exc) from exc

    def _coleccion_inexistente(self, operacion: str, exc: Exception) -> IndexingError:
        return IndexingError(
            f"La colección '{self.collection}' no existe en Qdrant. Ejecute "
            "`python -m rag_bbva.cli ingest`",
            detail=f"{operacion}: {exc}",
        )

    def ensure_collection(self, dimension: int, *, recreate: bool = False) -> None:
        def crear() -> None:
            existe = self.client.collection_exists(self.collection)
            if existe and recreate:
                self.client.delete_collection(self.collection)
                existe = False
            if not existe:
                self.client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(
                        size=dimension, distance=models.Distance.COSINE
                    ),
                )
                logger.info(
                    "Colección creada", extra={"coleccion": self.collection, "dim": dimension}
                )
            self.client.create_payload_index(
                self.collection, CAMPO_SECCION, field_schema=models.PayloadSchemaType.KEYWORD
            )

        self._llamar("crear colección", crear)

    def upsert(self, points: Sequence[VectorPoint]) -> None:
        for inicio in range(0, len(points), self.batch_size):
            lote = [
                models.PointStruct(id=p.id, vector=p.vector.tolist(), payload=p.payload)
                for p in points[inicio : inicio + self.batch_size]
            ]
            self._llamar(
                "upsert",
                lambda lote=lote: self.client.upsert(self.collection, points=lote, wait=True),
            )

    def search(
        self, vector: np.ndarray, top_k: int, *, section: str | None = None
    ) -> list[SearchHit]:
        filtro = (
            models.Filter(
                must=[
                    models.FieldCondition(key=CAMPO_SECCION, match=models.MatchValue(value=section))
                ]
            )
            if section
            else None
        )
        respuesta = self._llamar(
            "búsqueda",
            lambda: self.client.query_points(
                self.collection,
                query=vector.tolist(),
                limit=top_k,
                query_filter=filtro,
                with_payload=True,
            ),
        )
        return [
            SearchHit(id=str(p.id), score=float(p.score), payload=dict(p.payload or {}))
            for p in respuesta.points
        ]

    def count(self) -> int:
        respuesta = self._llamar("conteo", lambda: self.client.count(self.collection, exact=True))
        return int(respuesta.count)

    def delete(self, ids: Sequence[str]) -> None:
        for inicio in range(0, len(ids), self.batch_size):
            lote = list(ids[inicio : inicio + self.batch_size])
            self._llamar(
                "borrado",
                lambda lote=lote: self.client.delete(
                    self.collection, points_selector=models.PointIdsList(points=lote), wait=True
                ),
            )

    def stored_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        desde: Any = None
        while True:
            puntos, desde = self._llamar(
                "listado",
                lambda desde=desde: self.client.scroll(
                    self.collection,
                    limit=_LOTE_SCROLL,
                    offset=desde,
                    with_payload=[CAMPO_HASH],
                    with_vectors=False,
                ),
            )
            for punto in puntos:
                hashes[str(punto.id)] = str((punto.payload or {}).get(CAMPO_HASH, ""))
            if desde is None:
                return hashes
