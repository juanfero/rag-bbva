"""Exploración del sitio objetivo (M1).

Responde con evidencia las preguntas previas al scraping: qué permite `robots.txt`,
qué sitemaps existen, cuántas URLs hay por sección, qué tipos de contenido aparecen
y si el HTML estático basta o hace falta renderizar JavaScript.
"""

import logging
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from rag_bbva.exceptions import ScrapingError
from rag_bbva.scraping.discovery import SitemapSummary, collect_sitemaps, load_robots
from rag_bbva.scraping.fetcher import FetchResult, PoliteFetcher
from rag_bbva.scraping.page_analysis import (
    analyze_html,
    compare_texts,
    rendered_only_segments,
    visible_text,
)
from rag_bbva.scraping.sitemap import count_by_section, section_of
from rag_bbva.scraping.urls import extension_of

logger = logging.getLogger(__name__)


class Renderer(Protocol):
    """Renderiza una URL ejecutando JavaScript y devuelve el HTML resultante."""

    def render(self, url: str) -> str:
        """Devuelve el HTML tras ejecutar JavaScript."""
        ...


class RobotsSummary(BaseModel):
    """Resumen de `robots.txt` para nuestro User-Agent."""

    url: str
    status: int | None
    error: str | None = None
    sitemaps: list[str] = []
    applicable_user_agents: list[str] = []
    allow_rules: list[str] = []
    disallow_rules: list[str] = []
    crawl_delay: float | None = None
    fully_blocked_agents: list[str] = []


class UrlStats(BaseModel):
    """Estadísticas del conjunto de URLs declaradas en los sitemaps."""

    total_unique: int
    on_domain: int
    off_domain_hosts: dict[str, int]
    allowed_by_robots: int
    disallowed_by_robots: int
    disallowed_examples: list[str]
    by_section: dict[str, int]
    by_extension: dict[str, int]
    with_query: int
    wps_portal: int


class PageSample(BaseModel):
    """Página de la muestra con su descarga y su análisis."""

    section: str
    fetch: FetchResult
    title: str | None = None
    words: int | None = None
    scripts: int | None = None
    verdict: str | None = None
    placeholders: list[str] = []
    spa_markers: list[str] = []
    common_selectors: dict[str, int] = {}
    top_containers: list[dict[str, object]] = []
    rendered_coverage: float | None = None
    rendered_words: int | None = None
    only_rendered_sample: list[str] = []
    only_rendered_segments: list[str] = []
    render_error: str | None = None


class ExplorationReport(BaseModel):
    """Informe completo de la exploración."""

    generated_at: str
    base_url: str
    user_agent: str
    delay_seconds: float
    requests_made: int
    robots: RobotsSummary
    sitemaps: list[SitemapSummary]
    url_stats: UrlStats
    sample: list[PageSample]
    verdict_counts: dict[str, int]
    content_types: dict[str, int]
    status_codes: dict[str, int]
    redirects: dict[str, str]
    duplicate_titles: dict[str, list[str]]
    urls: list[str] = Field(default=[], exclude=True)


def choose_sample(urls: Iterable[str], size: int) -> list[str]:
    """Elige una muestra determinista y estratificada por sección.

    Recorre las secciones de mayor a menor tamaño tomando una URL por turno; dentro
    de cada sección toma URLs espaciadas uniformemente sobre la lista ordenada.
    """
    por_seccion: dict[str, list[str]] = {}
    for url in sorted(set(urls)):
        por_seccion.setdefault(section_of(url), []).append(url)
    secciones = sorted(por_seccion, key=lambda s: (-len(por_seccion[s]), s))

    cupo = dict.fromkeys(secciones, 0)
    total = min(size, sum(len(v) for v in por_seccion.values()))
    while sum(cupo.values()) < total:
        for seccion in secciones:
            if sum(cupo.values()) == total:
                break
            if cupo[seccion] < len(por_seccion[seccion]):
                cupo[seccion] += 1

    muestra: list[str] = []
    for seccion in secciones:
        lista, k = por_seccion[seccion], cupo[seccion]
        muestra.extend(lista[(i * len(lista)) // k] for i in range(k))
    return muestra


def _titulos_duplicados(muestra: Iterable[PageSample]) -> dict[str, list[str]]:
    """Títulos compartidos por varias URLs (posibles soft-404 o plantillas genéricas)."""
    por_titulo: dict[str, list[str]] = {}
    for pagina in muestra:
        if pagina.title:
            por_titulo.setdefault(pagina.title, []).append(pagina.fetch.url)
    return {t: urls for t, urls in por_titulo.items() if len(urls) > 1}


class SiteExplorer:
    """Orquesta la exploración: robots → sitemaps → estadísticas → muestra."""

    def __init__(
        self,
        fetcher: PoliteFetcher,
        *,
        base_url: str,
        user_agent: str,
        renderer: Renderer | None = None,
        max_sitemaps: int = 30,
    ) -> None:
        """Configura el explorador; `renderer` es opcional (comparación con JS)."""
        self._fetcher = fetcher
        self._base = base_url
        self._host = (urlsplit(base_url).hostname or "").lower()
        self._ua = user_agent
        self._renderer = renderer
        self._max_sitemaps = max_sitemaps

    def explore(self, sample_size: int = 10) -> ExplorationReport:
        """Ejecuta la exploración completa y devuelve el informe."""
        robots = self._leer_robots()
        sitemaps, urls = self._leer_sitemaps(robots.sitemaps)
        estadisticas = self._estadisticas(urls)
        candidatas = [u for u in urls if self._fetcher.is_allowed(u) and extension_of(u) != "pdf"]
        muestra = [self._analizar(u) for u in choose_sample(candidatas, sample_size)]

        tipos = Counter(
            (p.fetch.content_type or "sin respuesta").split(";")[0].strip() for p in muestra
        )
        estados = Counter(str(p.fetch.status or p.fetch.error or p.fetch.skipped) for p in muestra)
        return ExplorationReport(
            generated_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
            base_url=self._base,
            user_agent=self._ua,
            delay_seconds=self._fetcher.delay_seconds,
            requests_made=self._fetcher.requests_made,
            robots=robots,
            sitemaps=sitemaps,
            url_stats=estadisticas,
            sample=muestra,
            verdict_counts=dict(Counter(p.verdict or "sin análisis" for p in muestra)),
            content_types=dict(tipos),
            status_codes=dict(estados),
            redirects={
                p.fetch.url: p.fetch.final_url
                for p in muestra
                if p.fetch.final_url and p.fetch.final_url != p.fetch.url
            },
            duplicate_titles=_titulos_duplicados(muestra),
            urls=urls,
        )

    def _leer_robots(self) -> RobotsSummary:
        robots, resultado = load_robots(self._fetcher, self._base)

        grupos = robots.groups_for(self._ua)
        bloqueados = [
            ua
            for g in robots.groups
            if any(not r.allow and r.pattern == "/" for r in g.rules)
            and not any(r.allow for r in g.rules)
            for ua in g.user_agents
        ]
        return RobotsSummary(
            url=resultado.url,
            status=resultado.status,
            sitemaps=list(robots.sitemaps),
            applicable_user_agents=[ua for g in grupos for ua in g.user_agents],
            allow_rules=[r.pattern for g in grupos for r in g.rules if r.allow],
            disallow_rules=[r.pattern for g in grupos for r in g.rules if not r.allow],
            crawl_delay=robots.crawl_delay(self._ua),
            fully_blocked_agents=bloqueados,
        )

    def _leer_sitemaps(self, declarados: Iterable[str]) -> tuple[list[SitemapSummary], list[str]]:
        coleccion = collect_sitemaps(self._fetcher, self._base, declarados, self._max_sitemaps)
        return coleccion.summaries, coleccion.urls

    def _estadisticas(self, urls: list[str]) -> UrlStats:
        en_dominio = [u for u in urls if (urlsplit(u).hostname or "").lower() == self._host]
        externos = Counter(
            urlsplit(u).hostname or "(sin host)"
            for u in urls
            if (urlsplit(u).hostname or "").lower() != self._host
        )
        prohibidas = [u for u in en_dominio if not self._fetcher.is_allowed(u)]
        return UrlStats(
            total_unique=len(urls),
            on_domain=len(en_dominio),
            off_domain_hosts=dict(externos.most_common()),
            allowed_by_robots=len(en_dominio) - len(prohibidas),
            disallowed_by_robots=len(prohibidas),
            disallowed_examples=prohibidas[:10],
            by_section=count_by_section(en_dominio),
            by_extension=dict(Counter(extension_of(u) for u in en_dominio).most_common()),
            with_query=sum(1 for u in en_dominio if urlsplit(u).query),
            wps_portal=sum(1 for u in en_dominio if urlsplit(u).path.startswith("/wps/portal")),
        )

    def _analizar(self, url: str) -> PageSample:
        resultado = self._fetcher.fetch(url)
        muestra = PageSample(section=section_of(url), fetch=resultado)
        es_html = "html" in (resultado.content_type or "")
        if not (resultado.ok and es_html):
            return muestra

        html = resultado.content.decode("utf-8", errors="replace")
        analisis = analyze_html(html)
        muestra.title = analisis.title
        muestra.words = analisis.words
        muestra.scripts = analisis.scripts
        muestra.verdict = analisis.verdict
        muestra.placeholders = list(analisis.placeholders)
        muestra.spa_markers = list(analisis.spa_markers)
        muestra.common_selectors = analisis.common_selectors
        muestra.top_containers = [c.__dict__ for c in analisis.top_containers]

        if self._renderer is not None:
            self._fetcher.wait_turn()
            try:
                renderizado = self._renderer.render(url)
            except ScrapingError as exc:
                muestra.render_error = str(exc)
                return muestra
            comparacion = compare_texts(visible_text(html), visible_text(renderizado))
            muestra.rendered_coverage = comparacion.coverage
            muestra.rendered_words = comparacion.rendered_words
            muestra.only_rendered_sample = list(comparacion.only_rendered_sample)
            muestra.only_rendered_segments = list(rendered_only_segments(html, renderizado))
        return muestra
