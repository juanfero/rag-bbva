"""Interfaz de línea de comandos del proyecto.

Uso: `python -m rag_bbva.cli <comando>` o `rag-bbva <comando>`.
Comandos disponibles: `version`, `scrape` (M2), `clean` (M3) y `chunk` (M4). Los de
las etapas siguientes (ingest, chat, metrics) se agregan en sus módulos respectivos.
"""

import json
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Annotated
from urllib.parse import urlsplit

import httpx
import typer

from rag_bbva import __version__
from rag_bbva.config import get_settings
from rag_bbva.exceptions import ConfigurationError, IndexingError, ProcessingError, ScrapingError
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.indexing.pipeline import ChunkReport, chunk_documents, read_documents, write_chunks
from rag_bbva.logging_conf import configure_logging
from rag_bbva.processing.pipeline import CleaningPipeline, CleanReport, write_clean_output
from rag_bbva.scraping.base import MOTIVO_INTERRUMPIDO, CrawlReport
from rag_bbva.scraping.crawler import SitemapBfsCrawler
from rag_bbva.scraping.fetcher import PoliteFetcher
from rag_bbva.scraping.storage import RawStorage, text_fingerprint

# Código de salida cuando el crawl se aborta por posible bloqueo del sitio.
EXIT_ABORTADO = 2
# Código de salida cuando el crawl se interrumpe (Ctrl+C o SIGTERM), como 128 + SIGINT.
EXIT_INTERRUMPIDO = 130

app = typer.Typer(
    name="rag-bbva",
    help="Asistente RAG sobre el sitio público de Bancolombia.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Inicializa el logging antes de ejecutar cualquier comando."""
    configure_logging()


@app.command()
def version() -> None:
    """Muestra la versión instalada del paquete."""
    typer.echo(f"rag-bbva {__version__}")


def _resumen_crawl(reporte: CrawlReport) -> str:
    """Resumen legible de una ejecución del crawler."""
    lineas = [
        f"Duración: {reporte.duration_seconds} s · peticiones HTTP: {reporte.requests_made}",
        f"Semillas: {reporte.seeds} · procesadas: {reporte.processed} · "
        f"enlaces encolados: {reporte.links_enqueued}",
        f"Resultados: {reporte.outcomes}",
        f"Códigos HTTP: {reporte.status_codes}",
        f"Omitidas: {reporte.skipped}",
        f"Entradas en el manifest: {reporte.manifest_entries}",
    ]
    if reporte.aborted:
        lineas.append(f"ABORTADO: {reporte.abort_reason}")
    return "\n".join(lineas)


@contextmanager
def _sigterm_como_interrupcion() -> Iterator[None]:
    """Convierte SIGTERM en `KeyboardInterrupt` mientras dura el bloque, para que un
    `kill` cierre el crawl igual que Ctrl+C (manifest y reporte con lo avanzado)."""

    def _manejador(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    anterior = signal.signal(signal.SIGTERM, _manejador)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, anterior)


@app.command()
def scrape(
    max_pages: Annotated[
        int | None,
        typer.Option(min=1, help="Máximo de páginas a procesar (default: CRAWL_MAX_PAGES)."),
    ] = None,
) -> None:
    """Descarga el sitio (sitemaps + BFS) a RAW_DATA_DIR respetando robots.txt."""
    settings = get_settings()
    base_url = str(settings.target_base_url)
    ua = settings.crawl_user_agent
    storage = RawStorage(settings.raw_data_dir, fingerprint=text_fingerprint)

    with httpx.Client(headers={"User-Agent": ua}, timeout=settings.crawl_timeout_seconds) as client:
        fetcher = PoliteFetcher(
            client,
            user_agent=ua,
            allowed_host=urlsplit(base_url).hostname or "",
            delay_seconds=settings.crawl_delay_seconds,
            max_retries=settings.crawl_max_retries,
            backoff_seconds=settings.crawl_backoff_seconds,
        )
        crawler = SitemapBfsCrawler(
            fetcher,
            storage,
            base_url=base_url,
            max_pages=max_pages or settings.crawl_max_pages,
            max_depth=settings.crawl_max_depth,
            block_threshold=settings.crawl_block_threshold,
            exclude_path_prefixes=settings.crawl_exclude_path_prefixes,
        )
        try:
            with _sigterm_como_interrupcion():
                reporte = crawler.crawl()
        except ScrapingError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    (storage.root / "crawl_report.json").write_text(
        json.dumps(reporte.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    typer.echo(_resumen_crawl(reporte))
    typer.echo(f"Datos crudos: {storage.root} (manifest.jsonl, pages/, crawl_report.json)")
    if reporte.abort_reason == MOTIVO_INTERRUMPIDO:
        raise typer.Exit(code=EXIT_INTERRUMPIDO)
    if reporte.aborted:
        raise typer.Exit(code=EXIT_ABORTADO)


def _resumen_limpieza(reporte: CleanReport) -> str:
    """Resumen legible de una ejecución de la limpieza."""
    lineas = [
        f"Páginas leídas: {reporte.processed} · conservadas: {reporte.kept}",
        f"Descartadas: {reporte.discarded}",
        f"No leídas del manifest: {reporte.manifest_skipped}",
        f"Caracteres: {reporte.n_chars.model_dump() if reporte.n_chars else '-'}",
        f"Por sección: {reporte.by_section}",
        f"Por plantilla: {reporte.by_template}",
        f"Extracción: {reporte.by_extraction}",
        f"Idioma: {reporte.by_lang} · distinto de <html lang>: {reporte.lang_mismatch} "
        f"{reporte.lang_mismatch_pairs} · sin señal (lang de <html lang>): "
        f"{reporte.lang_fallback_by_template}",
        f"Fugas de boilerplate: {reporte.leaks.total} {reporte.leaks.by_pattern}",
    ]
    return "\n".join(lineas)


@app.command()
def clean() -> None:
    """Limpia el HTML de RAW_DATA_DIR y escribe documentos en CLEAN_DATA_DIR."""
    settings = get_settings()
    pipeline = CleaningPipeline.default(
        min_chars=settings.clean_min_chars,
        min_extraction_coverage=settings.clean_min_extraction_coverage,
    )
    try:
        resultado = pipeline.process_directory(settings.raw_data_dir)
        documentos, reporte = write_clean_output(resultado, settings.clean_data_dir)
    except ProcessingError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_resumen_limpieza(resultado.report))
    typer.echo(f"Datos limpios: {documentos} · reporte: {reporte}")


def _resumen_chunks(reporte: ChunkReport) -> str:
    """Resumen legible del chunking."""
    lineas = [
        f"Estrategia: {reporte.chunker} (tamaño {reporte.chunk_size}, "
        f"solapamiento {reporte.chunk_overlap})",
        f"Documentos: {reporte.documents} · chunks: {reporte.total}",
        f"Caracteres: {reporte.n_chars.model_dump() if reporte.n_chars else '-'}",
        f"Chunks por documento: "
        f"{reporte.chunks_per_document.model_dump() if reporte.chunks_per_document else '-'}",
        f"Por sección: {reporte.by_section}",
        f"Muy cortos (< {reporte.short_threshold} caracteres): {reporte.short_chunks}",
        f"Tokens: {reporte.tokens.model_dump() if reporte.tokens else '-'} · "
        f"máximo del modelo: {reporte.max_tokens} · lo superan: {reporte.over_max_tokens}",
    ]
    if reporte.over_max_tokens:
        lineas.append("ADVERTENCIA: hay chunks que el modelo truncaría; reduzca CHUNK_SIZE.")
    return "\n".join(lineas)


@app.command()
def chunk(
    strategy: Annotated[
        str | None,
        typer.Option(help="heading_aware | fixed_size (default: CHUNKING_STRATEGY)."),
    ] = None,
) -> None:
    """Trocea los documentos de CLEAN_DATA_DIR en chunks (CHUNKS_DATA_DIR)."""
    settings = get_settings()
    fabrica = ComponentFactory(settings)
    try:
        chunker = fabrica.create_chunker(strategy)
        documentos = read_documents(settings.clean_data_dir)
        # El embedder aporta el tokenizer real para verificar el máximo de tokens.
        resultado = chunk_documents(
            documentos, chunker, fabrica.create_embedder(), settings.chunk_min_chars
        )
        ruta_chunks, ruta_reporte = write_chunks(resultado, settings.chunks_data_dir)
    except (IndexingError, ConfigurationError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_resumen_chunks(resultado.report))
    typer.echo(f"Chunks: {ruta_chunks} · reporte: {ruta_reporte}")


if __name__ == "__main__":
    app()
