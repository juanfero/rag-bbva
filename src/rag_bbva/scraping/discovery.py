"""Descubrimiento de URLs: lectura de `robots.txt` y de los índices de sitemap.

Compartido por la exploración (M1) y el crawler (M2). En el sitio objetivo el índice
declarado en `robots.txt` y `/sitemap.xml` difieren, así que se leen ambos
(ver `docs/exploracion_sitio.md` §2).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urljoin

from pydantic import BaseModel

from rag_bbva.exceptions import ScrapingError
from rag_bbva.scraping.fetcher import FetchResult, PoliteFetcher
from rag_bbva.scraping.robots import RobotsTxt, parse_robots
from rag_bbva.scraping.sitemap import SitemapEntry, parse_sitemap

logger = logging.getLogger(__name__)


class SitemapSummary(BaseModel):
    """Resultado de descargar e interpretar un sitemap."""

    url: str
    status: int | None
    kind: str | None = None
    entries: int = 0
    error: str | None = None


@dataclass
class SitemapCollection:
    """Resultado de recorrer los sitemaps: resumen por sitemap y URLs únicas."""

    summaries: list[SitemapSummary] = field(default_factory=list)
    entries: list[SitemapEntry] = field(default_factory=list)

    @property
    def urls(self) -> list[str]:
        """URLs únicas en orden de aparición."""
        return [e.loc for e in self.entries]


def load_robots(fetcher: PoliteFetcher, base_url: str) -> tuple[RobotsTxt, FetchResult]:
    """Descarga e interpreta `robots.txt` y lo instala en el fetcher.

    Raises:
        ScrapingError: si `robots.txt` no responde 2xx; sin él no se puede verificar
            qué está permitido (S-08).
    """
    url = urljoin(base_url, "/robots.txt")
    resultado = fetcher.fetch(url)
    if not resultado.ok:
        raise ScrapingError(
            "No se pudo leer robots.txt; sin él no se puede verificar qué está permitido",
            detail=f"{url} → {resultado.status or resultado.error}",
        )
    robots = parse_robots(resultado.content.decode("utf-8", errors="replace"))
    fetcher.robots = robots
    return robots, resultado


def collect_sitemaps(
    fetcher: PoliteFetcher,
    base_url: str,
    declared: Iterable[str],
    max_sitemaps: int = 30,
) -> SitemapCollection:
    """Recorre los índices declarados y `/sitemap.xml`, sin repetir sitemaps ni URLs.

    Si una URL aparece en varios sitemaps se conserva la primera aparición, con el
    primer `lastmod` no vacío. Los errores de un sitemap se registran sin abortar.
    """
    pendientes = list(dict.fromkeys([*declared, urljoin(base_url, "/sitemap.xml")]))
    vistos: set[str] = set()
    coleccion = SitemapCollection()
    por_url: dict[str, SitemapEntry] = {}

    while pendientes and len(vistos) < max_sitemaps:
        url = pendientes.pop(0)
        if url in vistos:
            continue
        vistos.add(url)
        resultado = fetcher.fetch(url)
        resumen = SitemapSummary(url=url, status=resultado.status)
        coleccion.summaries.append(resumen)
        if not resultado.ok:
            resumen.error = resultado.error or resultado.skipped or f"HTTP {resultado.status}"
            continue
        try:
            sitemap = parse_sitemap(resultado.content)
        except ScrapingError as exc:
            resumen.error = str(exc)
            continue
        resumen.kind, resumen.entries = sitemap.kind, len(sitemap.entries)
        if sitemap.kind == "index":
            pendientes.extend(e.loc for e in sitemap.entries)
            continue
        for entrada in sitemap.entries:
            previa = por_url.get(entrada.loc)
            if previa is None or (previa.lastmod is None and entrada.lastmod):
                por_url[entrada.loc] = entrada

    if pendientes:
        logger.warning("Límite de sitemaps alcanzado", extra={"pendientes": len(pendientes)})
    coleccion.entries = list(por_url.values())
    return coleccion
