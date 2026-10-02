"""Crawler concreto: semillas de los sitemaps + BFS acotado por profundidad."""

import logging
from collections.abc import Iterable, Iterator

from bs4 import BeautifulSoup

from rag_bbva.scraping.base import BaseCrawler, CrawlTarget
from rag_bbva.scraping.discovery import SitemapSummary, collect_sitemaps, load_robots
from rag_bbva.scraping.fetcher import FetchResult, PoliteFetcher
from rag_bbva.scraping.robots import RobotsTxt
from rag_bbva.scraping.sitemap import interleave_by_section
from rag_bbva.scraping.storage import RawStorage
from rag_bbva.scraping.urls import is_html_candidate, normalize_url

logger = logging.getLogger(__name__)


class SitemapBfsCrawler(BaseCrawler):
    """Descubre semillas en **ambos** índices de sitemap y sigue enlaces internos.

    - `prepare`: lee `robots.txt` (sin él no se scrapea).
    - `discover_urls`: URLs de los sitemaps normalizadas, sin duplicados, solo HTML,
      del dominio y permitidas por robots, intercaladas por sección.
    - `extract_links`: enlaces `<a href>` internos y permitidos (profundidad + 1).
    """

    def __init__(
        self,
        fetcher: PoliteFetcher,
        storage: RawStorage,
        *,
        base_url: str,
        max_pages: int,
        max_depth: int,
        block_threshold: int,
        max_sitemaps: int = 30,
    ) -> None:
        """Configura el crawler sobre el sitio `base_url`."""
        super().__init__(
            fetcher,
            storage,
            max_pages=max_pages,
            max_depth=max_depth,
            block_threshold=block_threshold,
        )
        self.base_url = base_url
        self.max_sitemaps = max_sitemaps
        self.robots: RobotsTxt | None = None
        self.sitemap_summaries: list[SitemapSummary] = []

    def prepare(self) -> None:
        """Lee `robots.txt` e instala sus reglas en el fetcher."""
        self.robots, _ = load_robots(self.fetcher, self.base_url)

    def discover_urls(self) -> Iterator[CrawlTarget]:
        """Semillas desde el índice de robots.txt y `/sitemap.xml`."""
        declarados = self.robots.sitemaps if self.robots else ()
        coleccion = collect_sitemaps(self.fetcher, self.base_url, declarados, self.max_sitemaps)
        self.sitemap_summaries = coleccion.summaries

        lastmods: dict[str, str | None] = {}
        for entrada in coleccion.entries:
            url = normalize_url(entrada.loc)
            if url is None or not is_html_candidate(url):
                self.skipped["sitemap: URL no HTML o inválida"] += 1
                continue
            if not self.fetcher.is_allowed(url):
                self.skipped["sitemap: prohibida por robots.txt o fuera del dominio"] += 1
                continue
            if url in lastmods:
                self.skipped["sitemap: duplicada tras normalizar"] += 1
                continue
            lastmods[url] = entrada.lastmod

        logger.info(
            "Semillas descubiertas",
            extra={"semillas": len(lastmods), "sitemaps": len(self.sitemap_summaries)},
        )
        for url in interleave_by_section(lastmods):
            yield CrawlTarget(url=url, depth=0, source="sitemap", lastmod=lastmods[url])

    def extract_links(self, target: CrawlTarget, result: FetchResult) -> Iterable[str]:
        """Enlaces internos, HTML y permitidos de la página, normalizados y sin repetir."""
        base = result.final_url or target.url
        soup = BeautifulSoup(result.content, "lxml")
        enlaces: dict[str, None] = {}
        for ancla in soup.find_all("a", href=True):
            url = normalize_url(str(ancla["href"]), base=base)
            if url and is_html_candidate(url) and self.fetcher.is_allowed(url):
                enlaces[url] = None
        return list(enlaces)
