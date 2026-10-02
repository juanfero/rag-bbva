"""Pruebas de la CLI (M0)."""

import json
import os
import signal
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from rag_bbva import __version__
from rag_bbva.cli import EXIT_ABORTADO, EXIT_INTERRUMPIDO, app

runner = CliRunner()


def test_cli_version(clean_env: pytest.MonkeyPatch) -> None:
    """El comando `version` responde con la versión del paquete."""
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"rag-bbva {__version__}"


def test_cli_sin_argumentos_muestra_ayuda(clean_env: pytest.MonkeyPatch) -> None:
    """Sin comando se muestra la ayuda con los comandos disponibles."""
    result = runner.invoke(app, [])

    assert "version" in result.output
    assert "scrape" in result.output
    assert "clean" in result.output
    assert "chunk" in result.output
    assert "ingest" in result.output
    assert "search" in result.output
    # Regla del proyecto: todo texto visible al usuario dice Bancolombia (ADR-008).
    assert "Bancolombia" in result.output
    assert "BBVA" not in result.output


B = "https://www.banco.test"
SITEMAP = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    + "".join(f"<url><loc>{B}/personas/{i}</loc></url>" for i in range(5))
    + "</urlset>"
)


@pytest.fixture
def entorno_scrape(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Configura el CLI contra un sitio simulado, sin pausas reales."""
    clean_env.setenv("TARGET_BASE_URL", f"{B}/")
    clean_env.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    clean_env.setenv("CRAWL_DELAY_SECONDS", "0")
    clean_env.setenv("CRAWL_BACKOFF_SECONDS", "0")
    clean_env.setenv("CRAWL_BLOCK_THRESHOLD", "2")
    return tmp_path / "raw"


def _sitio(router: respx.MockRouter, estado_paginas: int = 200) -> None:
    router.get("/robots.txt").respond(200, text=f"User-agent: *\nSitemap: {B}/s.xml\n")
    router.get("/s.xml").respond(200, text=SITEMAP)
    router.get("/sitemap.xml").respond(404)
    router.get(url__regex=r"/personas/\d").respond(
        estado_paginas, headers={"content-type": "text/html"}, text="<html><p>hola</p></html>"
    )


def test_cli_scrape_respeta_max_pages(entorno_scrape: Path) -> None:
    """`scrape --max-pages 3` descarga 3 páginas y deja manifest, HTML y reporte."""
    with respx.mock(base_url=B) as router:
        _sitio(router)

        result = runner.invoke(app, ["scrape", "--max-pages", "3"])

    assert result.exit_code == 0, result.output
    assert "procesadas: 3" in result.stdout
    manifest = (entorno_scrape / "manifest.jsonl").read_text("utf-8").splitlines()
    assert len(manifest) == 3
    assert len(list((entorno_scrape / "pages").glob("*.html"))) == 3
    reporte = json.loads((entorno_scrape / "crawl_report.json").read_text("utf-8"))
    assert reporte["outcomes"] == {"guardada": 3}
    assert all(c.request.headers["User-Agent"] == "RAG-BBVA-TechTest/1.0" for c in router.calls)


def test_cli_scrape_abortado_sale_con_codigo_2(entorno_scrape: Path) -> None:
    """Una ráfaga de 403 aborta el crawl con código de salida distinto de 0."""
    with respx.mock(base_url=B) as router:
        _sitio(router, estado_paginas=403)

        result = runner.invoke(app, ["scrape"])

    assert result.exit_code == EXIT_ABORTADO
    assert "ABORTADO" in result.stdout


def test_cli_scrape_sin_robots_falla(entorno_scrape: Path) -> None:
    """Si robots.txt no está disponible el comando termina con error y no scrapea."""
    with respx.mock(base_url=B) as router:
        router.get("/robots.txt").mock(side_effect=httpx.ConnectError("caído"))

        result = runner.invoke(app, ["scrape"])

    assert result.exit_code == 1
    assert "robots.txt" in result.output
    assert not (entorno_scrape / "manifest.jsonl").exists()


FIXTURE_HTML = Path(__file__).parent.parent / "fixtures" / "html" / "plantilla_b_gmf_iva.html"


def test_cli_clean_escribe_documentos_y_reporte(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`clean` lee el manifest de RAW_DATA_DIR y deja documents.jsonl y clean_report.json."""
    raw, limpio = tmp_path / "raw", tmp_path / "clean"
    (raw / "pages").mkdir(parents=True)
    (raw / "pages" / "a.html").write_bytes(FIXTURE_HTML.read_bytes())
    entrada = {
        "url": f"{B}/acerca-de/gmf-iva", "final_url": f"{B}/acerca-de/gmf-iva", "status": 200,
        "outcome": "guardada", "fetched_at": "2026-10-02T00:00:00+00:00", "depth": 0,
        "source": "sitemap", "path": "pages/a.html",
    }  # fmt: skip
    (raw / "manifest.jsonl").write_text(json.dumps(entrada) + "\n", encoding="utf-8")
    clean_env.setenv("RAW_DATA_DIR", str(raw))
    clean_env.setenv("CLEAN_DATA_DIR", str(limpio))

    result = runner.invoke(app, ["clean"])

    assert result.exit_code == 0, result.output
    assert "conservadas: 1" in result.stdout
    documentos = (limpio / "documents.jsonl").read_text("utf-8").splitlines()
    assert json.loads(documentos[0])["template"] == "B_main_content"
    assert json.loads((limpio / "clean_report.json").read_text("utf-8"))["kept"] == 1


def test_cli_clean_sin_manifest_falla(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean_env.setenv("RAW_DATA_DIR", str(tmp_path / "vacio"))
    clean_env.setenv("CLEAN_DATA_DIR", str(tmp_path / "clean"))

    result = runner.invoke(app, ["clean"])

    assert result.exit_code == 1
    assert not (tmp_path / "clean").exists()


def test_cli_scrape_sigterm_cierra_reporte_con_lo_avanzado(entorno_scrape: Path) -> None:
    """Un SIGTERM a mitad del crawl deja manifest y crawl_report.json con `interrumpido`."""
    pedidas = 0
    manejador_previo = signal.getsignal(signal.SIGTERM)

    def pagina(_peticion: httpx.Request) -> httpx.Response:
        nonlocal pedidas
        pedidas += 1
        if pedidas == 3:
            os.kill(os.getpid(), signal.SIGTERM)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hola</p>")

    with respx.mock(base_url=B) as router:
        _sitio(router)
        router.get(url__regex=r"/personas/\d").mock(side_effect=pagina)

        result = runner.invoke(app, ["scrape"])

    assert result.exit_code == EXIT_INTERRUMPIDO, result.output
    reporte = json.loads((entorno_scrape / "crawl_report.json").read_text("utf-8"))
    assert reporte["aborted"] is True
    assert reporte["abort_reason"] == "interrumpido"
    assert 2 <= reporte["processed"] < 5
    manifest = (entorno_scrape / "manifest.jsonl").read_text("utf-8").splitlines()
    assert len(manifest) == reporte["manifest_entries"] == reporte["processed"]
    assert signal.getsignal(signal.SIGTERM) is manejador_previo  # se restaura al salir


def test_cli_chunk_escribe_chunks_y_reporte(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`chunk` trocea CLEAN_DATA_DIR y verifica tokens (aquí con el embedder falso)."""
    glosario = (Path(__file__).parent.parent / "fixtures" / "clean" / "glosario.json").read_text(
        "utf-8"
    )
    limpio = tmp_path / "clean"
    limpio.mkdir()
    (limpio / "documents.jsonl").write_text(json.dumps(json.loads(glosario)) + "\n", "utf-8")
    clean_env.setenv("CLEAN_DATA_DIR", str(limpio))
    clean_env.setenv("CHUNKS_DATA_DIR", str(tmp_path / "chunks"))
    clean_env.setenv("EMBEDDING_PROVIDER", "fake")

    result = runner.invoke(app, ["chunk", "--strategy", "fixed_size"])

    assert result.exit_code == 0, result.output
    assert "Estrategia: fixed_size" in result.stdout
    assert "lo superan: 0" in result.stdout
    reporte = json.loads((tmp_path / "chunks" / "chunk_report.json").read_text("utf-8"))
    assert reporte["total"] == len((tmp_path / "chunks" / "chunks.jsonl").read_text().splitlines())


def test_cli_chunk_errores(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean_env.setenv("CLEAN_DATA_DIR", str(tmp_path / "vacio"))
    clean_env.setenv("EMBEDDING_PROVIDER", "fake")

    assert runner.invoke(app, ["chunk"]).exit_code == 1
    assert runner.invoke(app, ["chunk", "--strategy", "semantico"]).exit_code == 1


def _entorno_ingest(clean_env: pytest.MonkeyPatch, tmp_path: Path, qdrant_url: str) -> None:
    glosario = (Path(__file__).parent.parent / "fixtures" / "clean" / "glosario.json").read_text(
        "utf-8"
    )
    limpio = tmp_path / "clean"
    limpio.mkdir()
    (limpio / "documents.jsonl").write_text(json.dumps(json.loads(glosario)) + "\n", "utf-8")
    clean_env.setenv("CLEAN_DATA_DIR", str(limpio))
    clean_env.setenv("EMBEDDING_PROVIDER", "fake")
    clean_env.setenv("EMBEDDINGS_CACHE_DIR", str(tmp_path / "emb"))
    clean_env.setenv("QDRANT_URL", qdrant_url)
    clean_env.setenv("QDRANT_TIMEOUT_SECONDS", "2")


def test_cli_ingest_en_memoria(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`ingest` con QDRANT_URL=":memory:" y el embedder falso: indexa y reporta."""
    _entorno_ingest(clean_env, tmp_path, ":memory:")

    result = runner.invoke(app, ["ingest", "--recreate"])

    assert result.exit_code == 0, result.output
    assert "reconstruida (--recreate)" in result.stdout
    assert "Embebidos: " in result.stdout and "desde caché: 0" in result.stdout
    assert (tmp_path / "emb" / "fake.npz").exists()


def test_cli_ingest_sin_qdrant_falla_con_mensaje_claro(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Puerto local cerrado (sin red externa): error claro y exit 1."""
    _entorno_ingest(clean_env, tmp_path, "http://127.0.0.1:9")

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 1
    assert "No se pudo conectar con Qdrant en http://127.0.0.1:9" in result.output


def test_cli_search_muestra_scores_y_umbral(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`search` imprime url, heading_path, coseno, score del reranker y el veredicto."""
    import numpy as np
    from qdrant_client import QdrantClient

    from rag_bbva.indexing.embedding import FakeEmbedder
    from rag_bbva.indexing.factory import ComponentFactory
    from rag_bbva.indexing.vector_store import QdrantVectorStore, VectorPoint, point_id
    from rag_bbva.retrieval.reranker import NoOpReranker
    from rag_bbva.retrieval.retriever import Retriever

    embedder = FakeEmbedder(dimension=32)
    store = QdrantVectorStore(QdrantClient(":memory:"), "c")
    store.ensure_collection(32)
    texto = "Un CDT es un certificado de depósito a término"
    store.upsert(
        [
            VectorPoint(
                point_id("cdt"),
                np.asarray(embedder.embed_documents([texto])[0]),
                {"chunk_id": "cdt", "doc_id": "d", "url": f"{B}/glosario", "title": "Glosario",
                 "heading_path": "Glosario > C > CDT", "text": texto, "section": "acerca-de"},
            )
        ]
    )  # fmt: skip
    capturados: dict[str, object] = {}

    def falso(
        self: ComponentFactory, *, rerank: bool | None = None, top_n: int | None = None
    ) -> Retriever:
        capturados.update(rerank=rerank, top_n=top_n)
        return Retriever(embedder=embedder, store=store, reranker=NoOpReranker(), top_n=top_n or 5)

    monkeypatch.setattr(ComponentFactory, "create_retriever", falso)

    result = runner.invoke(app, ["search", "¿qué es un CDT?", "--no-rerank", "--top-n", "2"])

    assert result.exit_code == 0, result.output
    assert capturados == {"rerank": False, "top_n": 2}
    assert f"{B}/glosario" in result.stdout
    assert "Glosario > C > CDT" in result.stdout
    assert "coseno" in result.stdout
    assert "Umbral: no se aplica sin reranker." in result.stdout


def test_cli_search_sin_qdrant_falla(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("QDRANT_URL", "http://127.0.0.1:9")
    clean_env.setenv("QDRANT_TIMEOUT_SECONDS", "2")
    clean_env.setenv("EMBEDDING_PROVIDER", "fake")

    result = runner.invoke(app, ["search", "hola", "--no-rerank"])

    assert result.exit_code == 1
    assert "No se pudo conectar con Qdrant" in result.output
