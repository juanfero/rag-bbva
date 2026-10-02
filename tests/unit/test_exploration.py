"""Pruebas del explorador del sitio con transporte HTTP simulado (M1, sin red)."""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from rag_bbva.exceptions import ScrapingError
from rag_bbva.scraping.exploration import SiteExplorer, choose_sample
from rag_bbva.scraping.fetcher import MAX_REDIRECCIONES, PoliteFetcher
from rag_bbva.scraping.urls import extension_of

FIXTURES = Path(__file__).parent.parent / "fixtures"
BASE = "https://www.banco.test/"
UA = "RAG-BBVA-TechTest/1.0"
HTML_ESTATICO = (FIXTURES / "html" / "estatica.html").read_bytes()

ROBOTS = b"""User-agent: GPTBot
Disallow: /

User-agent: *
Disallow: /personas/buscador
Disallow: /*.pdf$
Crawl-delay: 2
Sitemap: https://www.banco.test/sitemap-index.xml
"""
INDICE = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.banco.test/sitemap-personas.xml</loc></sitemap>
<sitemap><loc>https://www.banco.test/sitemap-roto.xml</loc></sitemap>
<sitemap><loc>https://otro.test/sitemap.xml</loc></sitemap>
</sitemapindex>"""
PERSONAS = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.banco.test/personas</loc></url>
<url><loc>https://www.banco.test/personas/cuentas</loc></url>
<url><loc>https://www.banco.test/personas/buscador</loc></url>
<url><loc>https://www.banco.test/empresas</loc></url>
<url><loc>https://www.banco.test/docs/tarifas.pdf</loc></url>
<url><loc>https://www.banco.test/personas?x=1</loc></url>
<url><loc>https://mi.banco.test/login</loc></url>
</urlset>"""


EXTRA = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.banco.test/sitemap-personas.xml</loc></sitemap>
<sitemap><loc>https://www.banco.test/sitemap-empresas.xml</loc></sitemap>
</sitemapindex>"""
EMPRESAS = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.banco.test/empresas/leasing</loc></url>
</urlset>"""


class Reloj:
    """Reloj falso: `sleep` avanza el tiempo y registra las pausas."""

    def __init__(self) -> None:
        self.ahora = 0.0
        self.pausas: list[float] = []

    def clock(self) -> float:
        return self.ahora

    def sleep(self, segundos: float) -> None:
        self.pausas.append(round(segundos, 3))
        self.ahora += segundos


def _sitio(peticiones: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    rutas = {
        "/robots.txt": (200, "text/plain", ROBOTS),
        "/sitemap-index.xml": (200, "application/xml", INDICE),
        "/sitemap-personas.xml": (200, "application/xml", PERSONAS),
        "/sitemap-roto.xml": (200, "text/html", b"<html>WAF</html>"),
        "/sitemap-empresas.xml": (200, "application/xml", EMPRESAS),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        if request.url.path in rutas:
            status, tipo, cuerpo = rutas[request.url.path]
            return httpx.Response(status, headers={"content-type": tipo}, content=cuerpo)
        if request.url.path == "/empresas":
            raise httpx.ConnectTimeout("timeout", request=request)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, headers={"content-type": "application/xml"}, content=EXTRA)
        if request.url.path == "/personas/cuentas":
            return httpx.Response(
                301, headers={"location": "https://www.banco.test/personas?utm_source=redirect"}
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, content=HTML_ESTATICO)

    return handler


def _fetcher(handler: Callable[[httpx.Request], httpx.Response], reloj: Reloj) -> PoliteFetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": UA})
    return PoliteFetcher(
        client,
        user_agent=UA,
        allowed_host="www.banco.test",
        delay_seconds=1.0,
        sleep=reloj.sleep,
        clock=reloj.clock,
    )


class RenderizadorFalso:
    """Devuelve el HTML estático con un párrafo extra, como si lo añadiera JS."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def render(self, url: str) -> str:
        self.urls.append(url)
        return HTML_ESTATICO.decode() + "<p>simulador interactivo cargado dinámicamente</p>"


@pytest.fixture
def entorno() -> tuple[SiteExplorer, list[httpx.Request], Reloj]:
    peticiones: list[httpx.Request] = []
    reloj = Reloj()
    explorer = SiteExplorer(_fetcher(_sitio(peticiones), reloj), base_url=BASE, user_agent=UA)
    return explorer, peticiones, reloj


def test_explore_resume_robots(entorno: tuple[SiteExplorer, list[httpx.Request], Reloj]) -> None:
    """Se reportan sitemaps, reglas del grupo aplicable y agentes bloqueados."""
    explorer, _, _ = entorno

    reporte = explorer.explore(sample_size=5)

    assert reporte.robots.sitemaps == ["https://www.banco.test/sitemap-index.xml"]
    assert reporte.robots.applicable_user_agents == ["*"]
    assert reporte.robots.disallow_rules == ["/personas/buscador", "/*.pdf$"]
    assert reporte.robots.crawl_delay == 2
    assert reporte.robots.fully_blocked_agents == ["GPTBot"]


def test_explore_recorre_sitemaps_y_registra_errores(
    entorno: tuple[SiteExplorer, list[httpx.Request], Reloj],
) -> None:
    """Se sigue el índice; sitemaps inválidos o externos quedan con error."""
    explorer, _, _ = entorno

    reporte = explorer.explore(sample_size=5)

    por_url = {s.url: s for s in reporte.sitemaps}
    assert por_url["https://www.banco.test/sitemap-index.xml"].kind == "index"
    assert por_url["https://www.banco.test/sitemap-personas.xml"].entries == 7
    assert por_url["https://www.banco.test/sitemap-roto.xml"].error is not None
    assert por_url["https://otro.test/sitemap.xml"].error == "fuera del dominio objetivo"


def test_explore_lee_sitemap_xml_ademas_del_declarado(
    entorno: tuple[SiteExplorer, list[httpx.Request], Reloj],
) -> None:
    """/sitemap.xml se consulta aunque robots declare otro índice; sin duplicar sitemaps."""
    explorer, peticiones, _ = entorno

    reporte = explorer.explore(sample_size=1)

    urls = [s.url for s in reporte.sitemaps]
    assert "https://www.banco.test/sitemap.xml" in urls
    assert "https://www.banco.test/sitemap-empresas.xml" in urls
    assert len(urls) == len(set(urls))
    pedidas = [str(p.url) for p in peticiones]
    assert pedidas.count("https://www.banco.test/sitemap-personas.xml") == 1


def test_explore_estadisticas_de_urls(
    entorno: tuple[SiteExplorer, list[httpx.Request], Reloj],
) -> None:
    """Conteos por sección, extensión, dominio externo y robots."""
    explorer, _, _ = entorno

    stats = explorer.explore(sample_size=5).url_stats

    assert stats.total_unique == 8
    assert stats.on_domain == 7
    assert stats.off_domain_hosts == {"mi.banco.test": 1}
    assert stats.by_section == {"personas": 4, "empresas": 2, "docs": 1}
    assert stats.by_extension == {"html (sin extensión)": 6, "pdf": 1}
    assert stats.disallowed_by_robots == 2
    assert stats.with_query == 1


def test_explore_respeta_robots_dominio_y_user_agent(
    entorno: tuple[SiteExplorer, list[httpx.Request], Reloj],
) -> None:
    """Nunca se piden URLs prohibidas, PDFs ni de otros dominios; siempre con nuestro UA."""
    explorer, peticiones, _ = entorno

    explorer.explore(sample_size=10)

    pedidas = {str(p.url) for p in peticiones}
    assert "https://www.banco.test/personas/buscador" not in pedidas
    assert "https://www.banco.test/docs/tarifas.pdf" not in pedidas
    assert all(p.url.host == "www.banco.test" for p in peticiones)
    assert {p.headers["User-Agent"] for p in peticiones} == {UA}


def test_explore_aplica_crawl_delay_entre_peticiones(
    entorno: tuple[SiteExplorer, list[httpx.Request], Reloj],
) -> None:
    """Tras leer robots.txt la pausa efectiva es max(config=1, Crawl-delay=2)."""
    explorer, peticiones, reloj = entorno

    reporte = explorer.explore(sample_size=10)

    # Cada salto de redirección es una petición propia y también respeta la pausa.
    assert reporte.delay_seconds == 2
    assert reporte.requests_made == len(peticiones)
    assert len(reloj.pausas) == len(peticiones) - 1
    assert all(p == 2 for p in reloj.pausas)


def test_explore_muestra_analiza_html_y_tolera_errores_de_red(
    entorno: tuple[SiteExplorer, list[httpx.Request], Reloj],
) -> None:
    """La muestra trae análisis por página; un timeout se registra sin romper."""
    explorer, _, _ = entorno

    reporte = explorer.explore(sample_size=10)

    por_url = {p.fetch.url: p for p in reporte.sample}
    assert set(por_url) == {
        "https://www.banco.test/personas",
        "https://www.banco.test/personas/cuentas",
        "https://www.banco.test/personas?x=1",
        "https://www.banco.test/empresas",
        "https://www.banco.test/empresas/leasing",
    }
    assert por_url["https://www.banco.test/personas"].verdict == "no_requiere_js"
    assert por_url["https://www.banco.test/empresas"].fetch.error.startswith("ConnectTimeout")
    assert reporte.verdict_counts == {"no_requiere_js": 4, "sin análisis": 1}
    assert reporte.content_types["text/html"] == 4


def test_explore_reporta_redirecciones_y_titulos_duplicados(
    entorno: tuple[SiteExplorer, list[httpx.Request], Reloj],
) -> None:
    """Se listan redirecciones (con utm_*) y títulos repetidos (posibles soft-404)."""
    explorer, _, _ = entorno

    reporte = explorer.explore(sample_size=10)

    assert reporte.redirects == {
        "https://www.banco.test/personas/cuentas": (
            "https://www.banco.test/personas?utm_source=redirect"
        )
    }
    assert len(reporte.duplicate_titles["Cuenta de Ahorros | Banco"]) == 4


def test_explore_compara_con_renderizado() -> None:
    """Con renderizador se calcula la cobertura del texto renderizado."""
    peticiones: list[httpx.Request] = []
    reloj = Reloj()
    renderizador = RenderizadorFalso()
    explorer = SiteExplorer(
        _fetcher(_sitio(peticiones), reloj), base_url=BASE, user_agent=UA, renderer=renderizador
    )

    reporte = explorer.explore(sample_size=1)

    pagina = reporte.sample[0]
    assert renderizador.urls == [pagina.fetch.url]
    assert pagina.rendered_coverage is not None
    assert 0.8 < pagina.rendered_coverage < 1
    assert "simulador" in pagina.only_rendered_sample
    assert pagina.only_rendered_segments == ["simulador interactivo cargado dinámicamente"]


def test_explore_sin_robots_lanza_error() -> None:
    """Si robots.txt no responde 2xx (p. ej. WAF 403) se detiene con ScrapingError."""

    def bloqueado(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"<html>Algo salio mal</html>")

    explorer = SiteExplorer(_fetcher(bloqueado, Reloj()), base_url=BASE, user_agent=UA)

    with pytest.raises(ScrapingError, match=r"robots\.txt"):
        explorer.explore()


def test_reporte_json_no_incluye_contenido_binario(
    entorno: tuple[SiteExplorer, list[httpx.Request], Reloj],
) -> None:
    """El informe serializado omite el HTML descargado y la lista completa de URLs."""
    explorer, _, _ = entorno

    datos = explorer.explore(sample_size=2).model_dump()

    assert "urls" not in datos
    assert all("content" not in p["fetch"] for p in datos["sample"])


@pytest.mark.parametrize(
    ("url", "ext"),
    [
        ("https://b.co/a/tarifas.PDF", "pdf"),
        ("https://b.co/a/b", "html (sin extensión)"),
        ("https://b.co/a/b.html?x=1", "html"),
        ("https://b.co/", "html (sin extensión)"),
    ],
)
def test_extension_of(url: str, ext: str) -> None:
    """Extensión del último segmento de la ruta."""
    assert extension_of(url) == ext


def test_choose_sample_estratificada_y_determinista() -> None:
    """Reparte la muestra entre secciones y siempre devuelve lo mismo."""
    urls = [f"https://b.co/personas/{i}" for i in range(10)]
    urls += [f"https://b.co/empresas/{i}" for i in range(4)]
    urls += ["https://b.co/acerca"]

    muestra = choose_sample(urls, 5)

    assert muestra == choose_sample(list(reversed(urls)), 5)
    assert len(muestra) == 5
    secciones = [u.split("/")[3] for u in muestra]
    assert secciones.count("personas") == 2
    assert secciones.count("empresas") == 2
    assert secciones.count("acerca") == 1


def test_choose_sample_mas_grande_que_el_universo() -> None:
    """Si se piden más URLs de las que hay, se devuelven todas sin repetir."""
    urls = ["https://b.co/a/1", "https://b.co/b/1"]

    assert sorted(choose_sample(urls, 10)) == urls


def _redirector(destinos: dict[str, str]) -> Callable[[httpx.Request], httpx.Response]:
    """Sitio que redirige según `destinos` y responde HTML en el resto de rutas."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=ROBOTS)
        if request.url.path in destinos:
            return httpx.Response(302, headers={"location": destinos[request.url.path]})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    return handler


def _fetcher_con_robots(destinos: dict[str, str]) -> tuple[PoliteFetcher, list[str]]:
    pedidas: list[str] = []
    base = _redirector(destinos)

    def handler(request: httpx.Request) -> httpx.Response:
        pedidas.append(str(request.url))
        return base(request)

    fetcher = _fetcher(handler, Reloj())
    explorer = SiteExplorer(fetcher, base_url=BASE, user_agent=UA)
    explorer._leer_robots()
    return fetcher, pedidas


def test_fetch_no_sigue_redireccion_a_otro_dominio() -> None:
    """Un 3xx hacia otro host no se sigue; se registra el destino y el motivo."""
    fetcher, pedidas = _fetcher_con_robots({"/fidu": "https://fiduciaria.banco.test/x"})

    resultado = fetcher.fetch("https://www.banco.test/fidu")

    assert resultado.status == 302
    assert resultado.final_url == "https://fiduciaria.banco.test/x"
    assert resultado.skipped == "redirige a una URL fuera del dominio objetivo"
    assert not any("fiduciaria" in u for u in pedidas)


def test_fetch_no_sigue_redireccion_prohibida_por_robots() -> None:
    """Un 3xx hacia una ruta prohibida por robots.txt no se sigue."""
    fetcher, pedidas = _fetcher_con_robots({"/a": "/personas/buscador"})

    resultado = fetcher.fetch("https://www.banco.test/a")

    assert resultado.skipped == "redirige a una URL prohibida por robots.txt"
    assert "https://www.banco.test/personas/buscador" not in pedidas


def test_fetch_sigue_redireccion_relativa_en_dominio() -> None:
    """Un 3xx relativo dentro del dominio se sigue y se reporta la URL final."""
    fetcher, _ = _fetcher_con_robots({"/viejo": "/nuevo?utm_source=redirect"})

    resultado = fetcher.fetch("https://www.banco.test/viejo")

    assert resultado.ok
    assert resultado.final_url == "https://www.banco.test/nuevo?utm_source=redirect"


def test_fetch_corta_bucles_de_redireccion() -> None:
    """Un bucle de redirecciones termina con error tras MAX_REDIRECCIONES saltos."""
    fetcher, pedidas = _fetcher_con_robots({"/a": "/b", "/b": "/a"})

    resultado = fetcher.fetch("https://www.banco.test/a")

    assert resultado.error == "demasiadas redirecciones"
    assert len(pedidas) == 1 + MAX_REDIRECCIONES + 1
