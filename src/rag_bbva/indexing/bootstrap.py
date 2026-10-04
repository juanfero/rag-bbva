"""Arranque del índice para Docker (M12): `docker compose up` no scrapea el sitio.

El servicio `init` del compose corre `python -m rag_bbva.cli bootstrap`, que:
1. Si no hay documentos limpios en `CLEAN_DATA_DIR`, copia los del snapshot versionado
   (`SNAPSHOT_DIR/documents.jsonl`).
2. Si no hay caché de embeddings para el modelo configurado, copia la del snapshot (así
   la primera ingesta no re-embebe ~3500 chunks en CPU).
3. Indexa en Qdrant **solo si la colección está vacía o no existe** (o con `force`). La
   ingesta ya es idempotente; saltarla evita trabajo en cada `docker compose up`.
4. Carga el embedder y el reranker, para que los modelos se descarguen una sola vez al
   volumen de modelos y la API arranque sin esperar la descarga.
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from rag_bbva.config import Settings
from rag_bbva.exceptions import IndexingError
from rag_bbva.indexing.ingest import Ingestor, IngestReport
from rag_bbva.indexing.pipeline import read_documents

logger = logging.getLogger(__name__)


class BootstrapReport(BaseModel):
    """Qué hizo el arranque."""

    documents_from_snapshot: bool
    embeddings_cache_from_snapshot: bool
    points_before: int
    indexed: bool
    ingest: IngestReport | None = None
    models_warmed: bool


@dataclass
class Bootstrapper:
    """Prepara datos, índice y modelos para que la API pueda responder."""

    settings: Settings
    factory: object  # ComponentFactory (evita una importación circular)
    log: list[str] = field(default_factory=list)

    def _copiar(self, origen: Path, destino: Path, que: str) -> bool:
        if destino.exists():
            return False
        if not origen.exists():
            raise IndexingError(
                f"No hay {que} ni snapshot para copiar", detail=f"falta {origen} y {destino}"
            )
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origen, destino)
        logger.info("Copiado desde el snapshot", extra={"que": que, "destino": str(destino)})
        return True

    def run(self, *, force: bool = False, warm_up: bool = True) -> BootstrapReport:
        ajustes, fabrica = self.settings, self.factory
        docs = self._copiar(
            ajustes.snapshot_dir / "documents.jsonl",
            ajustes.clean_data_dir / "documents.jsonl",
            "documentos limpios",
        )
        cache = fabrica.create_embedding_cache()  # type: ignore[attr-defined]
        try:
            cache_copiada = self._copiar(
                ajustes.snapshot_dir / "embeddings" / cache.path.name, cache.path, "caché"
            )
        except IndexingError:
            cache_copiada = False  # sin caché en el snapshot: se embebe en CPU
        store = fabrica.create_vector_store()  # type: ignore[attr-defined]
        try:
            puntos = store.count()
        except IndexingError as exc:
            if "no existe" not in exc.message:
                raise  # Qdrant caído u otro error: el arranque debe fallar
            puntos = 0
        indexar = force or puntos == 0
        reporte_ingesta = None
        embedder = fabrica.create_embedder()  # type: ignore[attr-defined]
        if indexar:
            ingestor = Ingestor(
                chunker=fabrica.create_chunker(),  # type: ignore[attr-defined]
                embedder=embedder,
                store=store,
                cache=fabrica.create_embedding_cache(),  # type: ignore[attr-defined]
                collection=ajustes.qdrant_collection,
            )
            reporte_ingesta = ingestor.run(read_documents(ajustes.clean_data_dir))
        if warm_up:
            embedder.embed_query("calentamiento")
            fabrica.create_reranker().warm_up()  # type: ignore[attr-defined]
        return BootstrapReport(
            documents_from_snapshot=docs,
            embeddings_cache_from_snapshot=cache_copiada,
            points_before=puntos,
            indexed=indexar,
            ingest=reporte_ingesta,
            models_warmed=warm_up,
        )
