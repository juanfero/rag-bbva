"""Arranque para Docker (M12): snapshot → índice solo si la colección está vacía."""

import json
import shutil
from pathlib import Path

import pytest

from rag_bbva.config import Settings
from rag_bbva.exceptions import IndexingError
from rag_bbva.indexing.bootstrap import Bootstrapper
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.indexing.vector_store import QdrantVectorStore, VectorStore

FIXTURE_DOC = Path(__file__).parent.parent / "fixtures" / "clean" / "glosario.json"
RAIZ = Path(__file__).resolve().parents[2]


class Fabrica(ComponentFactory):
    """Fábrica real con un almacén vectorial compartido entre corridas."""

    def __init__(self, settings: Settings, store: VectorStore) -> None:
        super().__init__(settings)
        self.store = store

    def create_vector_store(self) -> VectorStore:
        return self.store


def _settings(tmp_path: Path) -> Settings:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    doc = json.loads(FIXTURE_DOC.read_text("utf-8"))
    (snapshot / "documents.jsonl").write_text(json.dumps(doc, ensure_ascii=False) + "\n", "utf-8")
    return Settings(
        _env_file=None,
        embedding_provider="fake",
        reranker_enabled=False,
        snapshot_dir=snapshot,
        clean_data_dir=tmp_path / "data" / "clean",
        embeddings_cache_dir=tmp_path / "data" / "embeddings",
        qdrant_collection="prueba",
    )  # type: ignore[call-arg]


def test_primer_arranque_copia_el_snapshot_e_indexa(tmp_path: Path) -> None:
    ajustes = _settings(tmp_path)
    store = QdrantVectorStore.from_url(":memory:", "prueba")

    r = Bootstrapper(ajustes, Fabrica(ajustes, store)).run()

    assert r.documents_from_snapshot and (ajustes.clean_data_dir / "documents.jsonl").is_file()
    assert (r.points_before, r.indexed) == (0, True)  # la colección no existía
    assert r.ingest is not None and r.ingest.new == r.ingest.chunks > 0
    assert store.count() == r.ingest.chunks
    assert r.models_warmed


def test_segundo_arranque_no_reindexa_ni_pisa_los_datos(tmp_path: Path) -> None:
    ajustes = _settings(tmp_path)
    store = QdrantVectorStore.from_url(":memory:", "prueba")
    Bootstrapper(ajustes, Fabrica(ajustes, store)).run(warm_up=False)
    puntos = store.count()

    r = Bootstrapper(ajustes, Fabrica(ajustes, store)).run(warm_up=False)

    assert not r.documents_from_snapshot  # ya existían: no se pisan
    assert (r.points_before, r.indexed, r.ingest) == (puntos, False, None)
    forzado = Bootstrapper(ajustes, Fabrica(ajustes, store)).run(force=True, warm_up=False)
    assert forzado.indexed and forzado.ingest is not None and forzado.ingest.unchanged == puntos


def test_usa_la_cache_de_embeddings_del_snapshot(tmp_path: Path) -> None:
    ajustes = _settings(tmp_path)
    primero = Bootstrapper(ajustes, Fabrica(ajustes, QdrantVectorStore.from_url(":memory:", "p")))
    primero.run(warm_up=False)
    cache = next((tmp_path / "data" / "embeddings").glob("*.npz"))
    (ajustes.snapshot_dir / "embeddings").mkdir()
    shutil.copy(cache, ajustes.snapshot_dir / "embeddings" / cache.name)
    shutil.rmtree(tmp_path / "data")  # máquina nueva: solo queda el snapshot

    r = Bootstrapper(ajustes, Fabrica(ajustes, QdrantVectorStore.from_url(":memory:", "p"))).run(
        warm_up=False
    )

    assert r.embeddings_cache_from_snapshot
    assert (
        r.ingest is not None and r.ingest.from_cache == r.ingest.chunks and r.ingest.embedded == 0
    )


def test_sin_datos_ni_snapshot_falla_con_mensaje_claro(tmp_path: Path) -> None:
    ajustes = _settings(tmp_path)
    (ajustes.snapshot_dir / "documents.jsonl").unlink()
    with pytest.raises(IndexingError, match="No hay documentos limpios ni snapshot"):
        Bootstrapper(ajustes, Fabrica(ajustes, QdrantVectorStore.from_url(":memory:", "p"))).run()


def test_qdrant_caido_hace_fallar_el_arranque(tmp_path: Path) -> None:
    ajustes = _settings(tmp_path)

    class Caido(QdrantVectorStore):
        def count(self) -> int:
            raise IndexingError("No se pudo conectar con Qdrant en http://qdrant:6333")

    caido = Caido.from_url(":memory:", "p")
    with pytest.raises(IndexingError, match="No se pudo conectar"):
        Bootstrapper(ajustes, Fabrica(ajustes, caido)).run(warm_up=False)


def test_snapshot_versionado_es_coherente() -> None:
    """El snapshot del repo coincide con su manifiesto (archivos y SHA-256)."""
    import hashlib

    manifiesto = json.loads((RAIZ / "snapshot" / "MANIFEST.json").read_text("utf-8"))
    for nombre, datos in manifiesto["archivos"].items():
        archivo = RAIZ / "snapshot" / nombre
        assert archivo.stat().st_size == datos["bytes"], nombre
        assert hashlib.sha256(archivo.read_bytes()).hexdigest() == datos["sha256"], nombre
    lineas = (RAIZ / "snapshot" / "documents.jsonl").read_text("utf-8").splitlines()
    assert len(lineas) == manifiesto["documentos"] == 597
