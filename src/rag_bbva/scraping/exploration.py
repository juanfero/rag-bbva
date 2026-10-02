"""Exploración del sitio objetivo (M1).

Responde con evidencia las preguntas previas al scraping: qué permite `robots.txt`,
qué sitemaps existen, cuántas URLs hay por sección, qué tipos de contenido aparecen
y si el HTML estático basta o hace falta renderizar JavaScript.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from rag_bbva.exceptions import ScrapingError
from rag_bbva.scraping.page_analysis import (
    analyze_html,
    compare_texts,
    rendered_only_segments,
    visible_text,
)
from rag_bbva.scraping.robots import RobotsTxt, parse_robots
from rag_bbva.scraping.sitemap import count_by_section, parse_sitemap, section_of

logger = logging.getLogger(__name__)

MAX_REDIRECCIONES = 5


class FetchResult(BaseModel):
    """Resultado de una petición HTTP (o del motivo por el que no se hizo)."""

    url: str
    final_url: str | None = None
    status: int | None = None
    content_type: str | None = None
    size_bytes: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    skipped: str | None = None
    content: bytes = Field(default=b"", exclude=True, repr=False)

    @property
    def ok(self) -> bool:
        """La petición se hizo y devolvió 2xx."""
        return self.status is not None and 200 <= self.status < 300


class Renderer(Protocol):
    """Renderiza una URL ejecutando JavaScript y devuelve el HTML resultante."""

    def render(self, url: str) -> str:
        """Devuelve el HTML tras ejecutar JavaScript."""
        ...


class PoliteFetcher:
    """Cliente HTTP cortés: dominio acotado, `robots.txt`, User-Agent propio y pausa.

    La pausa efectiva entre peticiones es el máximo entre la configurada y el
    `Crawl-delay` de `robots.txt`.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        user_agent: str,
        allowed_host: str,
        delay_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Crea el fetcher sobre un `httpx.Client` ya configurado."""
        self._client = client
        self._user_agent = user_agent
        self._host = allowed_host.lower()
        self._delay = delay_seconds
        self._sleep = sleep
        self._clock = clock
        self._ultima: float | None = None
        self.robots: RobotsTxt | None = None
        self.requests_made = 0

    @property
    def delay_seconds(self) -> float:
        """Pausa efectiva entre peticiones."""
        robots_delay = self.robots.crawl_delay(self._user_agent) if self.robots else None
        return max(self._delay, robots_delay or 0.0)

    def is_allowed(self, url: str) -> bool:
        """URL dentro del dominio objetivo y permitida por `robots.txt`."""
        return self._motivo_bloqueo(url) is None

    def wait_turn(self) -> None:
        """Espera lo necesario para respetar la pausa desde la última petición."""
        if self._ultima is not None:
            restante = self.delay_seconds - (self._clock() - self._ultima)
            if restante > 0:
                self._sleep(restante)
        self._ultima = self._clock()

    def _motivo_bloqueo(self, url: str) -> str | None:
        """Motivo por el que no se puede pedir la URL, o `None` si está permitida."""
        if (urlsplit(url).hostname or "").lower() != self._host:
            return "fuera del dominio objetivo"
        if self.robots is not None and not self.robots.is_allowed(url, self._user_agent):
            return "prohibida por robots.txt"
        return None

    def fetch(self, url: str) -> FetchResult:
        """Descarga la URL si está permitida; nunca lanza por errores HTTP o de red.

        Las redirecciones se siguen a mano (máximo `MAX_REDIRECCIONES`) para validar
        cada salto contra el dominio y `robots.txt` y respetar la pausa en cada uno.
        """
        if motivo := self._motivo_bloqueo(url):
            return FetchResult(url=url, skipped=motivo)

        actual = url
        inicio = self._clock()
        for _ in range(MAX_REDIRECCIONES + 1):
            self.wait_turn()
            self.requests_made += 1
            try:
                respuesta = self._client.get(actual, follow_redirects=False)
            except httpx.HTTPError as exc:
                logger.warning("Fallo de red", extra={"url": actual, "error": repr(exc)})
                return FetchResult(url=url, final_url=actual, error=f"{type(exc).__name__}: {exc}")

            destino = respuesta.headers.get("location")
            if not (respuesta.is_redirect and destino):
                break
            siguiente = urljoin(actual, destino)
            if motivo := self._motivo_bloqueo(siguiente):
                return FetchResult(
                    url=url,
                    final_url=siguiente,
                    status=respuesta.status_code,
                    skipped=f"redirige a una URL {motivo}",
                )
            actual = siguiente
        else:
            return FetchResult(url=url, final_url=actual, error="demasiadas redirecciones")

        resultado = FetchResult(
            url=url,
            final_url=actual,
            status=respuesta.status_code,
            content_type=respuesta.headers.get("content-type"),
            size_bytes=len(respuesta.content),
            elapsed_ms=int((self._clock() - inicio) * 1000),
            content=respuesta.content,
        )
        logger.info(
            "Descarga",
            extra={"url": url, "status": resultado.status, "bytes": resultado.size_bytes},
        )
        return resultado


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


class SitemapSummary(BaseModel):
    """Resultado de descargar e interpretar un sitemap."""

    url: str
    status: int | None
    kind: str | None = None
    entries: int = 0
    error: str | None = None


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


def extension_of(url: str) -> str:
    """Extensión del último segmento de la ruta, o `html (sin extensión)`."""
    sufijo = PurePosixPath(urlsplit(url).path).suffix.lower().lstrip(".")
    return sufijo or "html (sin extensión)"


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
        url = urljoin(self._base, "/robots.txt")
        resultado = self._fetcher.fetch(url)
        if not resultado.ok:
            raise ScrapingError(
                "No se pudo leer robots.txt; sin él no se puede verificar qué está permitido",
                detail=f"{url} → {resultado.status or resultado.error}",
            )
        robots = parse_robots(resultado.content.decode("utf-8", errors="replace"))
        self._fetcher.robots = robots

        grupos = robots.groups_for(self._ua)
        bloqueados = [
            ua
            for g in robots.groups
            if any(not r.allow and r.pattern == "/" for r in g.rules)
            and not any(r.allow for r in g.rules)
            for ua in g.user_agents
        ]
        return RobotsSummary(
            url=url,
            status=resultado.status,
            sitemaps=list(robots.sitemaps),
            applicable_user_agents=[ua for g in grupos for ua in g.user_agents],
            allow_rules=[r.pattern for g in grupos for r in g.rules if r.allow],
            disallow_rules=[r.pattern for g in grupos for r in g.rules if not r.allow],
            crawl_delay=robots.crawl_delay(self._ua),
            fully_blocked_agents=bloqueados,
        )

    def _leer_sitemaps(self, declarados: Iterable[str]) -> tuple[list[SitemapSummary], list[str]]:
        # Además de los declarados en robots.txt se consulta siempre /sitemap.xml:
        # en el sitio objetivo ambos índices difieren (ver docs/exploracion_sitio.md).
        pendientes = list(dict.fromkeys([*declarados, urljoin(self._base, "/sitemap.xml")]))
        vistos: set[str] = set()
        resumenes: list[SitemapSummary] = []
        urls: dict[str, None] = {}

        while pendientes and len(vistos) < self._max_sitemaps:
            url = pendientes.pop(0)
            if url in vistos:
                continue
            vistos.add(url)
            resultado = self._fetcher.fetch(url)
            resumen = SitemapSummary(url=url, status=resultado.status)
            resumenes.append(resumen)
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
            else:
                urls.update(dict.fromkeys(e.loc for e in sitemap.entries))

        if pendientes:
            logger.warning("Límite de sitemaps alcanzado", extra={"pendientes": len(pendientes)})
        return resumenes, list(urls)

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
