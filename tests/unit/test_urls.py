"""Pruebas de normalización y filtros de URL (M2, sin red)."""

import pytest

from rag_bbva.scraping.sitemap import interleave_by_section
from rag_bbva.scraping.urls import excluded_prefix, is_html_candidate, normalize_url

B = "https://www.bancolombia.com"


@pytest.mark.parametrize(
    ("entrada", "esperada"),
    [
        (f"{B}/personas/", f"{B}/personas"),
        (f"{B}/personas#seccion", f"{B}/personas"),
        (
            f"{B}/negocios?utm_source=redirect&utm_medium=organic&utm_campaign=personas",
            f"{B}/negocios",
        ),
        (f"{B}/a?id=3&utm_source=x&gclid=1&q=b", f"{B}/a?id=3&q=b"),
        ("HTTPS://WWW.Bancolombia.COM/Personas", f"{B}/Personas"),
        ("https://www.bancolombia.com:443/x", f"{B}/x"),
        ("http://www.bancolombia.com:8080/x/", "http://www.bancolombia.com:8080/x"),
        (B, f"{B}/"),
        (f"{B}/", f"{B}/"),
        (f"  {B}/personas  ", f"{B}/personas"),
        (f"{B}/a?vacio=", f"{B}/a?vacio="),
    ],
)
def test_normalize_url(entrada: str, esperada: str) -> None:
    """Se quitan fragmentos, tracking, barra final, puerto por defecto y mayúsculas del host."""
    assert normalize_url(entrada) == esperada


def test_normalize_url_resuelve_relativas() -> None:
    """Las URLs relativas se resuelven contra la base."""
    assert normalize_url("../cuentas/", f"{B}/personas/ahorro/") == f"{B}/personas/cuentas"
    assert normalize_url("/empresas?utm_source=a", f"{B}/x") == f"{B}/empresas"


@pytest.mark.parametrize(
    "url", ["mailto:a@b.co", "tel:+5712345", "javascript:void(0)", "ftp://x.co/a", "/relativa"]
)
def test_normalize_url_descarta_no_http(url: str) -> None:
    """Esquemas no http(s) o URLs relativas sin base → None."""
    assert normalize_url(url) is None


def test_normalize_url_es_idempotente() -> None:
    """Normalizar dos veces da lo mismo."""
    url = normalize_url(f"{B}/a/?utm_source=x&id=2#f")
    assert url is not None
    assert normalize_url(url) == url


@pytest.mark.parametrize(
    ("url", "html"),
    [
        (f"{B}/personas", True),
        (f"{B}/pagina.html", True),
        (f"{B}/doc/tarifas.pdf", False),
        (f"{B}/personas/seguros/src/sass/main.sass", False),
        (f"{B}/personas/seguros/vehiculos/www.sura.com", False),
        (f"{B}/img/logo.PNG", False),
    ],
)
def test_is_html_candidate(url: str, html: bool) -> None:
    """Solo rutas sin extensión o con extensiones que sirven HTML."""
    assert is_html_candidate(url) is html


def test_interleave_by_section() -> None:
    """Alterna secciones conservando el orden interno y sin duplicados."""
    urls = [
        f"{B}/personas/1",
        f"{B}/personas/2",
        f"{B}/personas/3",
        f"{B}/empresas/1",
        f"{B}/personas/1",
        f"{B}/acerca-de/1",
    ]

    assert interleave_by_section(urls) == [
        f"{B}/personas/1",
        f"{B}/empresas/1",
        f"{B}/acerca-de/1",
        f"{B}/personas/2",
        f"{B}/personas/3",
    ]
    assert interleave_by_section([]) == []


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("https://b.test/acerca-de/sala-prensa/noticias/x", "/acerca-de/sala-prensa/"),
        ("https://b.test/acerca-de/sala-prensa", "/acerca-de/sala-prensa/"),
        ("https://b.test/acerca-de/sala-prensa-old", None),
        ("https://b.test/acerca-de/glosario", None),
        ("https://b.test/", None),
    ],
)
def test_excluded_prefix(url: str, esperado: str | None) -> None:
    """El prefijo con barra final cubre la ruta sin barra, pero no rutas que solo
    comparten el comienzo del nombre."""
    assert excluded_prefix(url, ["/otro/", "/acerca-de/sala-prensa/"]) == esperado


def test_excluded_prefix_sin_prefijos() -> None:
    assert excluded_prefix("https://b.test/x", []) is None
