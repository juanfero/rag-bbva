"""Pruebas de la ingesta idempotente y la caché de embeddings (M5, sin red ni modelos)."""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient

from rag_bbva.config import Settings
from rag_bbva.indexing.chunking import HeadingAwareChunker
from rag_bbva.indexing.embedding import FakeEmbedder
from rag_bbva.indexing.embedding_cache import EmbeddingCache, text_hash
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.indexing.ingest import Ingestor
from rag_bbva.indexing.vector_store import QdrantVectorStore, point_id
from rag_bbva.processing.models import CleanDocument

B = "https://www.bancolombia.com"


class EmbedderContador(FakeEmbedder):
    """FakeEmbedder que cuenta cuántos textos embebe."""

    def __init__(self) -> None:
        super().__init__(dimension=64)
        self.textos = 0
        self.llamadas = 0

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        self.llamadas += 1
        self.textos += len(texts)
        return super().embed_documents(texts)


def _doc(slug: str, texto: str, section: str = "personas") -> CleanDocument:
    url = f"{B}/{section}/{slug}"
    return CleanDocument(
        doc_id=f"id-{slug}", url=url, title=slug.title(), section=section, breadcrumbs=[],
        text=texto, html_lang="es", lang="es", lang_source="detectado", lastmod=None,
        published_at=None, scraped_at="2026-10-02T00:00:00+00:00", content_hash="h",
        n_chars=len(texto), template="A_main", extraction="selector",
    )  # fmt: skip


def _texto(tema: str, n: int) -> str:
    return "\n\n".join(
        f"## {tema} {i}\n\n" + f"Información sobre {tema} número {i}. " * 6 for i in range(n)
    )


DOCS = [
    _doc("tarjetas", _texto("tarjetas de crédito", 4)),
    _doc("cdt", _texto("certificado de depósito a término", 3)),
    _doc("leasing", _texto("leasing de vehículos", 3), section="negocios"),
]


Entorno = tuple[Ingestor, EmbedderContador, QdrantVectorStore]


@pytest.fixture
def entorno(tmp_path: Path) -> Entorno:
    embedder = EmbedderContador()
    store = QdrantVectorStore(QdrantClient(":memory:"), "docs", batch_size=4)
    ingestor = Ingestor(
        chunker=HeadingAwareChunker(300, 50),
        embedder=embedder,
        store=store,
        cache=EmbeddingCache(tmp_path / "emb", "fake"),
        collection="docs",
    )
    return ingestor, embedder, store


def test_primera_ingesta_indexa_todos_los_chunks(entorno: Entorno) -> None:
    ingestor, embedder, store = entorno

    reporte = ingestor.run(DOCS)

    total = sum(len(HeadingAwareChunker(300, 50).chunk(d)) for d in DOCS)
    assert reporte.chunks == total == reporte.new == reporte.points == store.count()
    assert reporte.embedded == embedder.textos == total
    assert (reporte.updated, reporte.unchanged, reporte.deleted, reporte.from_cache) == (0, 0, 0, 0)
    assert reporte.times.total >= reporte.times.embedding >= 0


def test_reingesta_sin_cambios_no_duplica_ni_reembebe(entorno: Entorno) -> None:
    ingestor, embedder, store = entorno
    primera = ingestor.run(DOCS)
    llamadas = embedder.llamadas

    segunda = ingestor.run(DOCS)

    assert segunda.points == primera.points == store.count()
    assert (segunda.new, segunda.updated, segunda.deleted, segunda.embedded) == (0, 0, 0, 0)
    assert segunda.unchanged == primera.chunks
    assert embedder.llamadas == llamadas  # no se llamó al modelo


def test_elimina_los_chunks_que_ya_no_existen(entorno: Entorno) -> None:
    ingestor, _, store = entorno
    ingestor.run(DOCS)
    chunks_cdt = HeadingAwareChunker(300, 50).chunk(DOCS[1])

    reporte = ingestor.run([DOCS[0], DOCS[2]])

    assert reporte.deleted == len(chunks_cdt)
    assert reporte.points == store.count() == reporte.chunks
    assert point_id(chunks_cdt[0].chunk_id) not in store.stored_hashes()
    assert reporte.embedded == 0


def test_documento_modificado_actualiza_y_solo_embebe_lo_nuevo(entorno: Entorno) -> None:
    ingestor, embedder, store = entorno
    ingestor.run(DOCS)
    antes = embedder.textos
    modificado = _doc(
        "tarjetas",
        _texto("tarjetas de crédito", 4) + "\n\n## Nueva sección\n\nTexto nuevo sobre cuotas.",
    )

    reporte = ingestor.run([modificado, DOCS[1], DOCS[2]])

    assert reporte.new + reporte.updated >= 1
    assert reporte.embedded == embedder.textos - antes
    assert reporte.embedded <= reporte.new + reporte.updated
    assert reporte.points == store.count() == reporte.chunks
    hits = store.search(ingestor.embedder.embed_query("nueva sección texto nuevo sobre cuotas"), 1)
    assert "Texto nuevo sobre cuotas" in hits[0].payload["text"]


def test_recreate_reconstruye_desde_la_cache(entorno: Entorno) -> None:
    ingestor, embedder, _ = entorno
    primera = ingestor.run(DOCS)
    llamadas = embedder.llamadas

    reporte = ingestor.run(DOCS, recreate=True)

    assert reporte.recreated
    assert reporte.new == reporte.from_cache == primera.chunks
    assert reporte.embedded == 0
    assert embedder.llamadas == llamadas
    assert reporte.points == primera.points


def test_payload_y_busqueda_con_filtro(entorno: Entorno) -> None:
    ingestor, _, store = entorno
    ingestor.run(DOCS)
    consulta = ingestor.embedder.embed_query("leasing de vehículos")

    sin_filtro = store.search(consulta, 1)[0]
    en_personas = store.search(consulta, 3, section="personas")

    assert sin_filtro.payload["section"] == "negocios"
    assert sin_filtro.payload["url"] == f"{B}/negocios/leasing"
    assert {
        "chunk_id",
        "doc_id",
        "title",
        "heading_path",
        "lang",
        "position",
        "text",
        "text_hash",
    } <= set(sin_filtro.payload)
    assert all(h.payload["section"] == "personas" for h in en_personas)


# ---------------------------------------------------------------- caché


def test_cache_persiste_entre_instancias(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path, "intfloat/multilingual-e5-small")
    cache.put(text_hash("hola"), np.ones(3))
    cache.save()

    otra = EmbeddingCache(tmp_path, "intfloat/multilingual-e5-small")

    assert otra.path.name == "intfloat--multilingual-e5-small.npz"
    assert np.array_equal(otra.get(text_hash("hola")), np.ones(3, dtype=np.float32))
    assert otra.dimension == 3
    assert len(otra) == 1
    assert list(tmp_path.glob(".*tmp*")) == []


def test_cache_ilegible_se_ignora(tmp_path: Path) -> None:
    (tmp_path / "fake.npz").write_bytes(b"no es un npz")

    cache = EmbeddingCache(tmp_path, "fake")

    assert len(cache) == 0
    assert cache.dimension is None


def test_cache_sin_cambios_no_escribe(tmp_path: Path) -> None:
    EmbeddingCache(tmp_path, "fake").save()

    assert not (tmp_path / "fake.npz").exists()


# ---------------------------------------------------------------- fábrica


def test_factory_crea_store_y_cache(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean_env.setenv("QDRANT_URL", ":memory:")
    clean_env.setenv("EMBEDDINGS_CACHE_DIR", str(tmp_path))
    fabrica = ComponentFactory(Settings(_env_file=None))

    store = fabrica.create_vector_store()
    store.ensure_collection(8)

    assert isinstance(store, QdrantVectorStore)
    assert store.collection == "bancolombia_docs"
    assert store.count() == 0
    assert fabrica.create_embedding_cache().path == tmp_path / "intfloat--multilingual-e5-small.npz"
