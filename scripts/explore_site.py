"""Diagnóstico del sitio objetivo antes de scrapear (M1).

Uso:
    python scripts/explore_site.py [--sample-size 10] [--render]

`--render` compara el HTML estático con el renderizado por un navegador headless.
Requiere Playwright, que no es dependencia del proyecto (ver ADR-009); se instala
de forma temporal en el entorno virtual:
    uv pip install playwright && playwright install chromium
    python scripts/explore_site.py --render
    uv pip uninstall playwright
"""

import contextlib
import json
import logging
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
import typer

from rag_bbva.config import get_settings
from rag_bbva.exceptions import ConfigurationError, ScrapingError
from rag_bbva.logging_conf import configure_logging
from rag_bbva.scraping.exploration import ExplorationReport, PoliteFetcher, SiteExplorer

logger = logging.getLogger("explore_site")
app = typer.Typer(add_completion=False)


class PlaywrightRenderer:
    """Renderiza páginas con Chromium headless usando nuestro User-Agent.

    Bloquea imágenes, fuentes y multimedia para reducir la carga sobre el sitio.
    """

    _BLOQUEADOS = frozenset({"image", "font", "media"})
    _ESPERA_RED_MS = 5000

    def __init__(self, user_agent: str, timeout_seconds: float) -> None:
        """Arranca el navegador; falla con un mensaje claro si falta Playwright."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ConfigurationError(
                "--render requiere Playwright",
                detail="instálalo temporalmente: uv pip install playwright && "
                "playwright install chromium",
            ) from exc
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=user_agent, locale="es-CO")
        self._context.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in self._BLOQUEADOS
                else route.continue_()
            ),
        )
        self._timeout_ms = int(timeout_seconds * 1000)

    def render(self, url: str) -> str:
        """Devuelve el HTML tras cargar la página y esperar a que la red se calme."""
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        page = self._context.new_page()
        try:
            page.goto(url, wait_until="load", timeout=self._timeout_ms)
            # La analítica mantiene conexiones abiertas: networkidle puede no llegar nunca.
            with contextlib.suppress(PlaywrightTimeoutError):
                page.wait_for_load_state("networkidle", timeout=self._ESPERA_RED_MS)
            return page.content()
        except PlaywrightError as exc:
            raise ScrapingError("No se pudo renderizar la página", detail=str(exc)) from exc
        finally:
            page.close()

    def close(self) -> None:
        """Cierra el navegador."""
        self._browser.close()
        self._pw.stop()


def _resumen(reporte: ExplorationReport) -> str:
    """Resumen legible del informe para la terminal."""
    stats = reporte.url_stats
    lineas = [
        f"Sitio: {reporte.base_url}  UA: {reporte.user_agent}  pausa: {reporte.delay_seconds}s",
        f"Peticiones realizadas: {reporte.requests_made}",
        f"robots.txt: HTTP {reporte.robots.status}; sitemaps: {reporte.robots.sitemaps}",
        f"  reglas Disallow para nuestro UA: {len(reporte.robots.disallow_rules)}; "
        f"agentes bloqueados por completo: {reporte.robots.fully_blocked_agents}",
        f"Sitemaps leídos: {len(reporte.sitemaps)} "
        f"(con error: {sum(1 for s in reporte.sitemaps if s.error)})",
        f"URLs únicas: {stats.total_unique} (dominio: {stats.on_domain}, "
        f"permitidas: {stats.allowed_by_robots}, prohibidas: {stats.disallowed_by_robots})",
        f"Por sección: {stats.by_section}",
        f"Por extensión: {stats.by_extension}",
        f"Muestra ({len(reporte.sample)}): veredictos {reporte.verdict_counts}; "
        f"estados {reporte.status_codes}",
    ]
    lineas.append(f"Redirecciones en la muestra: {reporte.redirects}")
    lineas.append(f"Títulos duplicados en la muestra: {reporte.duplicate_titles}")
    for p in reporte.sample:
        render = (
            f" cobertura_render={p.rendered_coverage}" if p.rendered_coverage is not None else ""
        )
        lineas.append(
            f"  [{p.section}] {p.fetch.status} {p.fetch.size_bytes}B "
            f"{p.words} palabras {p.verdict}{render} {p.fetch.url}"
        )
    return "\n".join(lineas)


@app.command()
def main(
    sample_size: Annotated[int, typer.Option(min=1, help="Páginas a muestrear.")] = 10,
    max_sitemaps: Annotated[int, typer.Option(min=1, help="Máximo de sitemaps a leer.")] = 30,
    render: Annotated[
        bool, typer.Option(help="Comparar con el HTML renderizado (Playwright).")
    ] = False,
    output_dir: Annotated[Path, typer.Option(help="Carpeta de salida.")] = Path("data/exploration"),
) -> None:
    """Explora el sitio configurado en TARGET_BASE_URL y guarda un informe JSON."""
    configure_logging()
    settings = get_settings()
    base_url = str(settings.target_base_url)
    ua = settings.crawl_user_agent

    renderer = PlaywrightRenderer(ua, settings.crawl_timeout_seconds) if render else None
    try:
        with httpx.Client(
            headers={"User-Agent": ua},
            timeout=settings.crawl_timeout_seconds,
        ) as client:
            fetcher = PoliteFetcher(
                client,
                user_agent=ua,
                allowed_host=urlsplit(base_url).hostname or "",
                delay_seconds=settings.crawl_delay_seconds,
            )
            explorer = SiteExplorer(fetcher, base_url=base_url, user_agent=ua, renderer=renderer)
            reporte = explorer.explore(sample_size=sample_size)
    except ScrapingError as exc:
        logger.error("Exploración abortada", extra={"error": str(exc)})
        raise typer.Exit(code=1) from exc
    finally:
        if renderer is not None:
            renderer.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(reporte.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "urls.txt").write_text("\n".join(reporte.urls) + "\n", encoding="utf-8")
    typer.echo(_resumen(reporte))
    typer.echo(f"\nInforme: {output_dir / 'report.json'}  URLs: {output_dir / 'urls.txt'}")


if __name__ == "__main__":
    app()
