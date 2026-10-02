"""Pruebas del pipeline de chunking, su reporte y la fábrica (M4, sin red ni modelos)."""

import json
from pathlib import Path

import pytest

from rag_bbva.config import Settings
from rag_bbva.exceptions import ConfigurationError, IndexingError
from rag_bbva.indexing.chunking import FixedSizeChunker, HeadingAwareChunker
from rag_bbva.indexing.embedding import FakeEmbedder
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.indexing.pipeline import chunk_documents, read_chunks, read_documents, write_chunks
from rag_bbva.processing.models import CleanDocument

FIXTURES = Path(__file__).parent.parent / "fixtures"
B = "https://www.bancolombia.com"


def _doc(texto: str, url: str) -> CleanDocument:
    return CleanDocument(
        doc_id="corto", url=url, title="Corto", section="personas", breadcrumbs=[], text=texto,
        html_lang="es", lang="es", lang_source="detectado", lastmod=None, published_at=None,
        scraped_at="2026-10-02T00:00:00+00:00", content_hash="h", n_chars=len(texto),
        template="A_main", extraction="selector",
    )  # fmt: skip


@pytest.fixture(scope="module")
def glosario() -> CleanDocument:
    return CleanDocument.model_validate(
        json.loads((FIXTURES / "clean" / "glosario.json").read_text("utf-8"))
    )


# ---------------------------------------------------------------- pipeline y reporte


def test_chunk_documents_reporte(glosario: CleanDocument) -> None:
    corto = _doc("# Hola\n\nTexto breve.", f"{B}/personas/corto")

    resultado = chunk_documents(
        [glosario, corto], HeadingAwareChunker(800, 120), FakeEmbedder(), 100
    )
    reporte = resultado.report

    assert reporte.documents == 2
    assert reporte.total == len(resultado.chunks)
    assert reporte.n_chars is not None and reporte.n_chars.max <= 800
    assert reporte.by_section == {"acerca-de": reporte.total - 1, "personas": 1}
    assert reporte.short_chunks >= 1
    assert any(e.url.endswith("/corto") for e in reporte.short_examples)
    assert reporte.over_max_tokens == 0
    assert reporte.documents_with_most_chunks[glosario.url] == reporte.total - 1


def test_chunk_documents_detecta_chunks_que_superan_el_maximo_de_tokens(
    glosario: CleanDocument,
) -> None:
    reporte = chunk_documents(
        [glosario], HeadingAwareChunker(800, 120), FakeEmbedder(max_tokens=50), 100
    ).report

    assert reporte.max_tokens == 50
    assert reporte.over_max_tokens > 0
    assert len(reporte.over_max_tokens_ids) <= 10


def test_escritura_y_lectura_de_chunks(glosario: CleanDocument, tmp_path: Path) -> None:
    resultado = chunk_documents([glosario], FixedSizeChunker(800, 120), FakeEmbedder(), 100)

    ruta_chunks, ruta_reporte = write_chunks(resultado, tmp_path)

    assert read_chunks(tmp_path) == resultado.chunks
    assert json.loads(ruta_reporte.read_text("utf-8"))["chunker"] == "fixed_size"
    assert ruta_chunks.read_text("utf-8").count("\n") == len(resultado.chunks)


def test_lecturas_sin_archivos_lanzan_indexing_error(tmp_path: Path) -> None:
    with pytest.raises(IndexingError, match="clean"):
        read_documents(tmp_path)
    with pytest.raises(IndexingError, match="chunk"):
        read_chunks(tmp_path)


# ---------------------------------------------------------------- fábrica


def test_factory_crea_el_chunker_configurado(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CHUNKING_STRATEGY", "fixed_size")
    clean_env.setenv("CHUNK_SIZE", "500")
    clean_env.setenv("CHUNK_OVERLAP", "50")
    fabrica = ComponentFactory(Settings(_env_file=None))

    chunker = fabrica.create_chunker()

    assert isinstance(chunker, FixedSizeChunker)
    assert (chunker.chunk_size, chunker.chunk_overlap) == (500, 50)
    assert isinstance(fabrica.create_chunker("heading_aware"), HeadingAwareChunker)
    with pytest.raises(ConfigurationError):
        fabrica.create_chunker("semantico")
