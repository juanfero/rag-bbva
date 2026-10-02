"""Interfaz de línea de comandos del proyecto.

Uso: `python -m rag_bbva.cli <comando>` o `rag-bbva <comando>`.
Comandos disponibles: `version` y `scrape` (M2). Los de las etapas siguientes
(clean, ingest, chat, metrics) se agregan en sus módulos respectivos.
"""

import json
from typing import Annotated
from urllib.parse import urlsplit

import httpx
import typer

from rag_bbva import __version__
from rag_bbva.config import get_settings
from rag_bbva.exceptions import ScrapingError
from rag_bbva.logging_conf import configure_logging
from rag_bbva.scraping.base import CrawlReport
from rag_bbva.scraping.crawler import SitemapBfsCrawler
from rag_bbva.scraping.fetcher import PoliteFetcher
from rag_bbva.scraping.storage import RawStorage, text_fingerprint

# Código de salida cuando el crawl se aborta por posible bloqueo del sitio.
EXIT_ABORTADO = 2

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
        )
        try:
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
    if reporte.aborted:
        raise typer.Exit(code=EXIT_ABORTADO)


if __name__ == "__main__":
    app()
