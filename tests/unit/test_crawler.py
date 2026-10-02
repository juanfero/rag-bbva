"""Pruebas del crawler (Template Method + SitemapBfsCrawler) con respx (M2, sin red)."""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

import httpx
import pytest
import respx

from rag_bbva.exceptions import ScrapingError
from rag_bbva.scraping.base import BaseCrawler, BlockGuard, CrawlTarget
from rag_bbva.scraping.crawler import SitemapBfsCrawler
from rag_bbva.scraping.fetcher import PoliteFetcher
from rag_bbva.scraping.storage import ManifestEntry, RawStorage, text_fingerprint

B = "https://www.banco.test"
UA = "RAG-BBVA-TechTest/1.0"

ROBOTS = f"""User-agent: GPTBot
Disallow: /

User-agent: *
Disallow: /personas/buscador
Disallow: /*pdf*
Sitemap: {B}/sitemap-index.xml
"""


def _indice(*hijos: str) -> str:
    cuerpo = "".join(f"<sitemap><loc>{B}/{h}</loc></sitemap>" for h in hijos)
    return (
        f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{cuerpo}</sitemapindex>'
    )


def _urlset(*urls: str | tuple[str, str]) -> str:
    partes = []
    for u in urls:
        loc, lastmod = (u, None) if isinstance(u, str) else u
        extra = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        partes.append(f"<url><loc>{B}{loc}</loc>{extra}</url>")
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{"".join(partes)}</urlset>'


def _html(titulo: str, *enlaces: str) -> str:
    anclas = "".join(f'<a href="{e}">{e}</a>' for e in enlaces)
    cabeza = f"<head><title>{titulo}</title></head>"
    return f"<html>{cabeza}<body><main>{titulo}</main>{anclas}</body></html>"


PERSONAS = _urlset(
    ("/personas", "2025-08-21"),
    "/personas/cuentas/",
    "/personas/cuentas?utm_source=newsletter",
    "/personas/buscador",
    "/docs/tarifas.pdf",
    "/personas/src/sass/main.sass",
    "/personas/no-existe",
    "/personas/caida",
    "/personas/lenta",
)
EMPRESAS = _urlset("/empresas", "/empresas/fidu", "/negocios", "/empresas/logo")


class Reloj:
    """Reloj falso: `sleep` avanza el tiempo y registra las esperas."""

    def __init__(self) -> None:
        self.ahora = 0.0
        self.esperas: list[float] = []

    def clock(self) -> float:
        return self.ahora

    def sleep(self, segundos: float) -> None:
        self.esperas.append(round(segundos, 3))
        self.ahora += segundos


def _html_response(texto: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=texto)


@pytest.fixture
def sitio() -> Iterator[respx.MockRouter]:
    """Sitio simulado con todos los casos del plan."""
    with respx.mock(base_url=B, assert_all_called=False) as router:
        router.get("/robots.txt").respond(200, text=ROBOTS)
        router.get("/sitemap-index.xml").respond(200, text=_indice("sitemap-personas.xml"))
        router.get("/sitemap.xml").respond(
            200, text=_indice("sitemap-personas.xml", "sitemap-empresas.xml")
        )
        router.get("/sitemap-personas.xml").respond(200, text=PERSONAS)
        router.get("/sitemap-empresas.xml").respond(200, text=EMPRESAS)
        router.get("/personas").mock(
            return_value=_html_response(
                _html(
                    "Personas",
                    "/personas/nueva",
                    "/personas/cuentas#top",
                    "https://otro.test/x",
                    "/personas/buscador",
                    "mailto:a@b.co",
                    "/doc.pdf",
                )
            )
        )
        router.get("/personas/cuentas").mock(return_value=_html_response(_html("Cuentas")))
        router.get("/personas/nueva").mock(
            return_value=_html_response(_html("Nueva", "/personas/profunda"))
        )
        router.get("/personas/profunda").mock(return_value=_html_response(_html("Profunda")))
        router.get("/personas/no-existe").respond(404, text="no")
        router.get("/personas/caida").respond(503)
        router.get("/personas/lenta").mock(
            side_effect=[httpx.ReadTimeout("lento"), _html_response(_html("Lenta"))]
        )
        router.get("/personas/buscador").respond(200, text="no debería pedirse")
        router.get("/empresas").respond(
            301, headers={"location": f"{B}/negocios?utm_source=redirect&utm_campaign=empresas"}
        )
        router.get("/negocios").mock(return_value=_html_response(_html("Negocios")))
        router.get("/empresas/fidu").respond(301, headers={"location": "https://fidu.banco.test/x"})
        router.get("/empresas/logo").respond(
            200, headers={"content-type": "image/png"}, content=b"PNG"
        )
        yield router


def _crawler(
    raw: Path,
    reloj: Reloj | None = None,
    *,
    max_pages: int = 100,
    max_depth: int = 1,
    block_threshold: int = 5,
    max_retries: int = 2,
) -> SitemapBfsCrawler:
    reloj = reloj or Reloj()
    fetcher = PoliteFetcher(
        httpx.Client(headers={"User-Agent": UA}),
        user_agent=UA,
        allowed_host="www.banco.test",
        delay_seconds=1.0,
        max_retries=max_retries,
        backoff_seconds=2.0,
        sleep=reloj.sleep,
        clock=reloj.clock,
    )
    return SitemapBfsCrawler(
        fetcher,
        RawStorage(raw, fingerprint=text_fingerprint),
        base_url=f"{B}/",
        max_pages=max_pages,
        max_depth=max_depth,
        block_threshold=block_threshold,
    )


def _manifest(raw: Path) -> dict[str, ManifestEntry]:
    return RawStorage(raw).load_manifest()


def _pedidas(router: respx.MockRouter) -> list[str]:
    return [str(llamada.request.url) for llamada in router.calls]


def _rutas_pedidas(router: respx.MockRouter) -> list[str]:
    return [llamada.request.url.path for llamada in router.calls]


def _guardadas_con_final(manifest: dict[str, ManifestEntry], final: str) -> list[ManifestEntry]:
    return [e for e in manifest.values() if e.final_url == final and e.path is not None]


# ---------------------------------------------------------------- descubrimiento


def test_lee_los_dos_indices_de_sitemap(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Las URLs de empresas solo están en /sitemap.xml y también se descargan."""
    crawler = _crawler(tmp_path)

    crawler.crawl()

    urls = [s.url for s in crawler.sitemap_summaries]
    assert f"{B}/sitemap-index.xml" in urls
    assert f"{B}/sitemap.xml" in urls
    assert f"{B}/empresas" in _manifest(tmp_path)
    assert len(_guardadas_con_final(_manifest(tmp_path), f"{B}/negocios")) == 1


def test_semillas_normalizadas_filtradas_e_intercaladas(
    sitio: respx.MockRouter, tmp_path: Path
) -> None:
    """Sin duplicados, sin PDF/sass, sin rutas de robots, alternando secciones."""
    crawler = _crawler(tmp_path)
    crawler.prepare()

    semillas = [t.url for t in crawler.discover_urls()]

    assert semillas[:4] == [
        f"{B}/personas",
        f"{B}/empresas",
        f"{B}/negocios",
        f"{B}/personas/cuentas",
    ]
    assert semillas.count(f"{B}/personas/cuentas") == 1
    assert f"{B}/personas/buscador" not in semillas
    assert not any(s.endswith((".pdf", ".sass")) for s in semillas)
    assert crawler.skipped["sitemap: URL no HTML o inválida"] == 2
    assert crawler.skipped["sitemap: prohibida por robots.txt o fuera del dominio"] == 1
    assert crawler.skipped["sitemap: duplicada tras normalizar"] == 1


# ---------------------------------------------------------------- cortesía y alcance


def test_respeta_robots_txt(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Una URL prohibida (en sitemap y en enlaces) nunca se descarga."""
    _crawler(tmp_path).crawl()

    assert "/personas/buscador" not in _rutas_pedidas(sitio)
    assert f"{B}/personas/buscador" not in _manifest(tmp_path)


def test_no_sale_del_dominio_ni_sigue_redirecciones_externas(
    sitio: respx.MockRouter, tmp_path: Path
) -> None:
    """Ni enlaces ni redirecciones llevan a otro host."""
    _crawler(tmp_path).crawl()

    assert all(u.startswith(B) for u in _pedidas(sitio))
    fidu = _manifest(tmp_path)[f"{B}/empresas/fidu"]
    assert fidu.outcome == "redireccion_omitida"
    assert fidu.final_url == "https://fidu.banco.test/x"
    assert fidu.path is None


def test_no_repite_urls_normalizadas(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """/personas/cuentas/, ?utm_source y #top se descargan una sola vez."""
    _crawler(tmp_path).crawl()

    assert _pedidas(sitio).count(f"{B}/personas/cuentas") == 1


def test_redireccion_interna_no_duplica_html(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """/empresas → /negocios?utm_…: una sola descarga y una sola copia de /negocios.

    La semilla /negocios, ya obtenida vía redirección, se omite sin pedirla otra vez.
    """
    reporte = _crawler(tmp_path).crawl()

    manifest = _manifest(tmp_path)
    empresas = manifest[f"{B}/empresas"]
    assert empresas.final_url == f"{B}/negocios"
    assert empresas.outcome == "guardada"
    assert _rutas_pedidas(sitio).count("/negocios") == 1
    assert len(_guardadas_con_final(manifest, f"{B}/negocios")) == 1
    assert reporte.skipped["ya descargada vía redirección"] == 1


def test_pausa_entre_todas_las_peticiones(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Con latencia cero, cada petición tras la primera espera al menos 1 s."""
    reloj = Reloj()
    crawler = _crawler(tmp_path, reloj)

    crawler.crawl()

    assert reloj.ahora >= (crawler.fetcher.requests_made - 1) * 1.0
    assert {r.headers["User-Agent"] for r in (c.request for c in sitio.calls)} == {UA}


# ---------------------------------------------------------------- límites


def test_respeta_max_pages(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Con max_pages=3 se procesan exactamente 3 páginas."""
    reporte = _crawler(tmp_path, max_pages=3).crawl()

    assert reporte.processed == 3
    assert len(_manifest(tmp_path)) == 3


def test_respeta_max_depth(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Profundidad 1: se sigue /personas/nueva pero no /personas/profunda."""
    reporte = _crawler(tmp_path, max_depth=1).crawl()

    manifest = _manifest(tmp_path)
    assert manifest[f"{B}/personas/nueva"].depth == 1
    assert manifest[f"{B}/personas/nueva"].source == "enlace"
    assert f"{B}/personas/profunda" not in manifest
    assert reporte.links_enqueued == 1


def test_max_depth_cero_no_sigue_enlaces(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Profundidad 0: solo semillas del sitemap."""
    _crawler(tmp_path, max_depth=0).crawl()

    assert f"{B}/personas/nueva" not in _manifest(tmp_path)


# ---------------------------------------------------------------- errores


def test_503_reintenta_y_se_rinde_sin_romper_el_crawl(
    sitio: respx.MockRouter, tmp_path: Path
) -> None:
    """Un 503 persistente se reintenta 1 + N veces, se registra y el crawl sigue."""
    reporte = _crawler(tmp_path, max_retries=2).crawl()

    caida = _manifest(tmp_path)[f"{B}/personas/caida"]
    assert _pedidas(sitio).count(f"{B}/personas/caida") == 3
    assert caida.outcome == "error_http"
    assert caida.status == 503
    assert caida.attempts == 3
    assert not reporte.aborted
    # /personas/lenta es la última semilla (posterior a /personas/caida): el crawl siguió.
    assert _manifest(tmp_path)[f"{B}/personas/lenta"].outcome == "guardada"


def test_timeout_transitorio_se_recupera(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Un timeout seguido de 200 termina guardando la página."""
    _crawler(tmp_path).crawl()

    lenta = _manifest(tmp_path)[f"{B}/personas/lenta"]
    assert lenta.outcome == "guardada"
    assert lenta.attempts == 2


def test_404_se_registra_sin_guardar_html(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Un 404 queda en el manifest sin archivo HTML."""
    _crawler(tmp_path).crawl()

    no_existe = _manifest(tmp_path)[f"{B}/personas/no-existe"]
    assert no_existe.outcome == "error_http"
    assert no_existe.status == 404
    assert no_existe.path is None
    assert no_existe.attempts == 1
    assert not (tmp_path / RawStorage(tmp_path).relative_path(no_existe.url)).exists()


def test_error_http_con_cuerpo_pequeno_guarda_fragmento(tmp_path: Path) -> None:
    """Un 403 de S3 (AccessDenied, 111 B) deja su cuerpo resumido en el manifest."""
    cuerpo = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>"
    )
    with respx.mock(base_url=B) as router:
        router.get("/robots.txt").respond(200, text=f"User-agent: *\nSitemap: {B}/s.xml\n")
        router.get("/s.xml").respond(200, text=_urlset("/negocios/viejo", "/negocios/grande"))
        router.get("/sitemap.xml").respond(404)
        router.get("/negocios/viejo").respond(
            403, headers={"content-type": "application/xml"}, text=cuerpo
        )
        router.get("/negocios/grande").respond(500, text="x" * 5000)

        _crawler(tmp_path, max_retries=0).crawl()

    manifest = _manifest(tmp_path)
    viejo = manifest[f"{B}/negocios/viejo"].error
    assert viejo is not None
    assert viejo.startswith("HTTP 403: <?xml")
    assert "<Code>AccessDenied</Code>" in viejo
    assert manifest[f"{B}/negocios/grande"].error == "HTTP 500"


def test_contenido_no_html_no_se_guarda(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Un 200 con image/png se registra como no_html."""
    _crawler(tmp_path).crawl()

    logo = _manifest(tmp_path)[f"{B}/empresas/logo"]
    assert logo.outcome == "no_html"
    assert logo.path is None


def test_robots_inaccesible_detiene_el_crawl(tmp_path: Path) -> None:
    """Si robots.txt responde 403 (WAF) no se descarga nada más."""
    with respx.mock(base_url=B) as router:
        router.get("/robots.txt").respond(403, text="Algo salió mal")

        with pytest.raises(ScrapingError, match=r"robots\.txt"):
            _crawler(tmp_path).crawl()

        assert len(router.calls) == 1


def test_rafaga_de_403_aborta_y_guarda_lo_avanzado(tmp_path: Path) -> None:
    """N respuestas 403 seguidas cortan el crawl; el manifest conserva lo hecho."""
    paginas = [f"/personas/p{i}" for i in range(10)]
    with respx.mock(base_url=B) as router:
        router.get("/robots.txt").respond(200, text=f"User-agent: *\nSitemap: {B}/s.xml\n")
        router.get("/s.xml").respond(200, text=_urlset(*paginas))
        router.get("/sitemap.xml").respond(404)
        router.get("/personas/p0").mock(return_value=_html_response(_html("ok")))
        router.get(url__regex=r"/personas/p[1-9]").respond(403)

        reporte = _crawler(tmp_path, block_threshold=3).crawl()

    assert reporte.aborted
    assert "403/429" in (reporte.abort_reason or "")
    assert reporte.processed == 4
    assert len(_manifest(tmp_path)) == 4
    assert _manifest(tmp_path)[f"{B}/personas/p0"].outcome == "guardada"


def test_block_guard_429_cuenta_y_un_200_reinicia() -> None:
    """403 y 429 suman a la racha; un 2xx/404 la reinicia; un fallo de red no cuenta."""
    guard = BlockGuard(threshold=3)

    assert [guard.record(s) for s in (429, 403, None, 200, 403, 429, 404, 403)] == [
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    ]
    assert guard.record(429) is False
    assert guard.record(403) is True


# ---------------------------------------------------------------- manifest e incremental


def test_manifest_esquema_y_archivos(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Cada línea tiene el esquema esperado; las guardadas apuntan a su HTML."""
    _crawler(tmp_path).crawl()

    lineas = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text("utf-8").splitlines()]
    campos = {
        "url", "final_url", "status", "content_type", "outcome", "fetched_at", "depth",
        "source", "lastmod", "content_hash", "size_bytes", "elapsed_ms", "attempts",
        "path", "duplicate_of", "error",
    }  # fmt: skip
    assert all(set(linea) == campos for linea in lineas)

    personas = _manifest(tmp_path)[f"{B}/personas"]
    assert personas.lastmod == "2025-08-21"
    assert personas.source == "sitemap"
    assert personas.depth == 0
    assert personas.status == 200
    assert personas.path is not None
    assert personas.path.startswith("pages/")
    assert personas.path.endswith(".html")
    html = (tmp_path / personas.path).read_text("utf-8")
    assert "<title>Personas</title>" in html
    assert personas.content_hash is not None
    assert len(personas.content_hash) == 64


def test_reejecucion_incremental(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Segunda corrida: lo que no cambió queda sin_cambios y no se reescribe."""
    _crawler(tmp_path).crawl()
    personas = _manifest(tmp_path)[f"{B}/personas"]
    assert personas.path is not None
    archivo = tmp_path / personas.path
    mtime = archivo.stat().st_mtime_ns
    sitio.get("/personas/cuentas").mock(return_value=_html_response(_html("Cuentas v2")))
    sitio.get("/personas/lenta").mock(return_value=_html_response(_html("Lenta")))

    reporte = _crawler(tmp_path).crawl()

    manifest = _manifest(tmp_path)
    assert manifest[f"{B}/personas"].outcome == "sin_cambios"
    assert archivo.stat().st_mtime_ns == mtime
    assert manifest[f"{B}/personas/cuentas"].outcome == "guardada"
    assert reporte.outcomes["sin_cambios"] >= 1


def test_incremental_ignora_marcado_volatil(tmp_path: Path) -> None:
    """Como en Bancolombia: cada respuesta trae ids aleatorios; si el texto no cambia,
    la segunda corrida marca sin_cambios y no reescribe el HTML."""
    contador = iter(range(100))

    def pagina(request: httpx.Request) -> httpx.Response:
        volatil = f'<script data-rpid="{next(contador)}"></script><link id="L{next(contador)}">'
        return _html_response(f"<html><head>{volatil}</head><body><p>Texto fijo</p></body></html>")

    with respx.mock(base_url=B) as router:
        router.get("/robots.txt").respond(200, text=f"User-agent: *\nSitemap: {B}/s.xml\n")
        router.get("/s.xml").respond(200, text=_urlset("/personas/a"))
        router.get("/sitemap.xml").respond(404)
        router.get("/personas/a").mock(side_effect=pagina)

        _crawler(tmp_path).crawl()
        primera = _manifest(tmp_path)[f"{B}/personas/a"]
        reporte = _crawler(tmp_path).crawl()

    segunda = _manifest(tmp_path)[f"{B}/personas/a"]
    assert primera.outcome == "guardada"
    assert segunda.outcome == "sin_cambios"
    assert segunda.content_hash == primera.content_hash
    assert reporte.outcomes == {"sin_cambios": 1}


def test_crawl_parcial_conserva_el_manifest_previo(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """Un crawl con max_pages=2 no borra las entradas de un crawl anterior completo."""
    _crawler(tmp_path).crawl()
    total = len(_manifest(tmp_path))

    reporte = _crawler(tmp_path, max_pages=2).crawl()

    assert reporte.processed == 2
    assert reporte.manifest_entries == total


def test_reporte_resume_la_ejecucion(sitio: respx.MockRouter, tmp_path: Path) -> None:
    """El reporte cuenta semillas, resultados, estados y omisiones."""
    reporte = _crawler(tmp_path).crawl()

    assert reporte.seeds == 9
    assert reporte.processed == len(_manifest(tmp_path))
    assert reporte.outcomes["error_http"] == 2
    assert reporte.status_codes["404"] == 1
    assert reporte.skipped["ya descargada vía redirección"] == 1
    assert reporte.requests_made == len(sitio.calls)


# ---------------------------------------------------------------- Template Method


class CrawlerMinimo(BaseCrawler):
    """Subclase mínima: solo define `discover_urls`; el resto lo pone la plantilla."""

    def __init__(self, fetcher: PoliteFetcher, storage: RawStorage, urls: Iterable[str]) -> None:
        super().__init__(fetcher, storage, max_pages=10, max_depth=0, block_threshold=5)
        self._urls = list(urls)

    def discover_urls(self) -> Iterable[CrawlTarget]:
        return [CrawlTarget(u, 0, "sitemap") for u in self._urls]


def test_template_method_con_subclase_minima(tmp_path: Path) -> None:
    """Una subclase que solo descubre URLs hereda descarga, validación y persistencia."""
    with respx.mock(base_url=B) as router:
        router.get("/a").mock(return_value=_html_response(_html("A")))
        fetcher = PoliteFetcher(
            httpx.Client(), user_agent=UA, allowed_host="www.banco.test", delay_seconds=0
        )

        reporte = CrawlerMinimo(fetcher, RawStorage(tmp_path), [f"{B}/a"]).crawl()

    assert reporte.outcomes == {"guardada": 1}
    assert _manifest(tmp_path)[f"{B}/a"].path is not None
