"""Ingesta (M5): documentos limpios → chunks → embeddings → base vectorial.

Sincronización completa e idempotente:
- cada chunk se guarda con un id determinista (`uuid5` de su `chunk_id`);
- los chunks cuyo texto no cambió no se vuelven a embeber ni a escribir;
- los puntos de chunks que ya no existen se borran;
- los vectores se toman de la caché por hash del texto antes de llamar al modelo.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from rag_bbva.indexing.chunking import ChunkingStrategy
from rag_bbva.indexing.embedding import Embedder
from rag_bbva.indexing.embedding_cache import EmbeddingCache, text_hash
from rag_bbva.indexing.models import Chunk
from rag_bbva.indexing.vector_store import CAMPO_HASH, VectorPoint, VectorStore, point_id
from rag_bbva.processing.models import CleanDocument

logger = logging.getLogger(__name__)

# Campos del chunk que viajan en el payload (todo menos el texto con encabezado).
_CAMPOS_PAYLOAD = (
    "chunk_id", "doc_id", "url", "title", "section", "heading_path", "lang", "position",
    "n_chars", "chunker", "text",
)  # fmt: skip


class StageTimes(BaseModel):
    """Segundos por etapa."""

    chunking: float
    sync_plan: float
    embedding: float
    upsert: float
    delete: float
    total: float


class IngestReport(BaseModel):
    """Resumen de una ingesta."""

    collection: str
    chunker: str
    recreated: bool
    documents: int
    chunks: int
    new: int
    updated: int
    unchanged: int
    deleted: int
    embedded: int
    from_cache: int
    points: int
    times: StageTimes


def chunk_payload(chunk: Chunk, hash_texto: str) -> dict[str, object]:
    """Payload del punto: texto para citar, metadatos y hash del texto embebido."""
    payload: dict[str, object] = {c: getattr(chunk, c) for c in _CAMPOS_PAYLOAD}
    payload[CAMPO_HASH] = hash_texto
    return payload


@dataclass
class Ingestor:
    """Orquesta chunking, caché, embedder y almacén vectorial."""

    chunker: ChunkingStrategy
    embedder: Embedder
    store: VectorStore
    cache: EmbeddingCache
    collection: str = ""

    def run(self, documents: Sequence[CleanDocument], *, recreate: bool = False) -> IngestReport:
        """Sincroniza la colección con los documentos y devuelve el reporte."""
        t0 = time.perf_counter()
        chunks = [c for doc in documents for c in self.chunker.chunk(doc)]
        t_chunks = time.perf_counter()

        # Con vectores en caché no hace falta cargar el modelo solo para saber la dimensión.
        dimension = self.cache.dimension or self.embedder.dimension
        self.store.ensure_collection(dimension, recreate=recreate)
        guardados = self.store.stored_hashes()
        actuales = {point_id(c.chunk_id): (c, text_hash(c.embedding_text)) for c in chunks}
        nuevos = [i for i in actuales if i not in guardados]
        actualizados = [i for i in actuales if i in guardados and guardados[i] != actuales[i][1]]
        obsoletos = [i for i in guardados if i not in actuales]
        pendientes = nuevos + actualizados
        t_plan = time.perf_counter()

        vectores, desde_cache, sin_vector = {}, 0, []
        for i in pendientes:
            vector = self.cache.get(actuales[i][1])
            if vector is None:
                sin_vector.append(i)
            else:
                vectores[i] = vector
                desde_cache += 1
        if sin_vector:
            calculados = self.embedder.embed_documents(
                [actuales[i][0].embedding_text for i in sin_vector]
            )
            for i, vector in zip(sin_vector, calculados, strict=True):
                vectores[i] = vector
                self.cache.put(actuales[i][1], vector)
            self.cache.save()
        t_embed = time.perf_counter()

        self.store.upsert(
            [
                VectorPoint(i, vectores[i], chunk_payload(actuales[i][0], actuales[i][1]))
                for i in pendientes
            ]
        )
        t_upsert = time.perf_counter()
        self.store.delete(obsoletos)
        t_delete = time.perf_counter()

        reporte = IngestReport(
            collection=self.collection,
            chunker=self.chunker.name,
            recreated=recreate,
            documents=len(documents),
            chunks=len(actuales),
            new=len(nuevos),
            updated=len(actualizados),
            unchanged=len(actuales) - len(pendientes),
            deleted=len(obsoletos),
            embedded=len(sin_vector),
            from_cache=desde_cache,
            points=self.store.count(),
            times=StageTimes(
                chunking=round(t_chunks - t0, 2),
                sync_plan=round(t_plan - t_chunks, 2),
                embedding=round(t_embed - t_plan, 2),
                upsert=round(t_upsert - t_embed, 2),
                delete=round(t_delete - t_upsert, 2),
                total=round(time.perf_counter() - t0, 2),
            ),
        )
        logger.info("Ingesta terminada", extra=reporte.model_dump(exclude={"times"}))
        return reporte
