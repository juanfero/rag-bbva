"""Pruebas del parser de sitemaps y de la derivación de secciones (M1, sin red)."""

import gzip
from pathlib import Path

import pytest

from rag_bbva.exceptions import ScrapingError
from rag_bbva.scraping.sitemap import (
    SECCION_RAIZ,
    SitemapEntry,
    count_by_section,
    parse_sitemap,
    section_of,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sitemaps"


def test_parse_indice() -> None:
    """Un sitemapindex devuelve sus sitemaps hijos con lastmod opcional."""
    sitemap = parse_sitemap((FIXTURES / "index.xml").read_bytes())

    assert sitemap.kind == "index"
    assert sitemap.entries == (
        SitemapEntry("https://www.bancolombia.com/sitemap-personas.xml", "2025-08-21"),
        SitemapEntry("https://www.bancolombia.com/sitemap-empresas.xml", None),
    )


def test_parse_urlset_limpia_espacios_e_ignora_entradas_vacias() -> None:
    """Se recortan espacios en <loc>, se ignoran <url> sin loc y las extensiones."""
    sitemap = parse_sitemap((FIXTURES / "urlset.xml").read_bytes())

    assert sitemap.kind == "urlset"
    assert [e.loc for e in sitemap.entries] == [
        "https://www.bancolombia.com/personas",
        "https://www.bancolombia.com/personas/cuentas/ahorros",
        "https://www.bancolombia.com/wps/portal/empresas/financiacion",
    ]
    assert sitemap.entries[0].lastmod == "2025-08-21"


def test_parse_sin_namespace() -> None:
    """Sitemaps sin namespace también se aceptan."""
    sitemap = parse_sitemap(b"<urlset><url><loc>https://x.co/a</loc></url></urlset>")

    assert sitemap.entries == (SitemapEntry("https://x.co/a"),)


def test_parse_gzip() -> None:
    """Un sitemap .xml.gz se descomprime de forma transparente."""
    contenido = gzip.compress((FIXTURES / "urlset.xml").read_bytes())

    assert len(parse_sitemap(contenido).entries) == 3


@pytest.mark.parametrize(
    "contenido",
    [
        b"<html><body>Bloqueado</body></html>",
        b"esto no es xml",
        b"",
        b"\x1f\x8b corrupto",
    ],
)
def test_parse_invalido_lanza_scraping_error(contenido: bytes) -> None:
    """HTML (p. ej. una página de WAF), basura o vacío → ScrapingError."""
    with pytest.raises(ScrapingError):
        parse_sitemap(contenido)


def test_parse_no_resuelve_entidades_externas(tmp_path: Path) -> None:
    """Protección XXE: no se expande el contenido de archivos locales."""
    secreto = tmp_path / "secreto.txt"
    secreto.write_text("DATO-SECRETO", encoding="utf-8")
    xml = (
        f'<?xml version="1.0"?><!DOCTYPE u [<!ENTITY x SYSTEM "file://{secreto}">]>'
        "<urlset><url><loc>https://x.co/&x;</loc></url></urlset>"
    ).encode()

    sitemap = parse_sitemap(xml)

    assert all("DATO-SECRETO" not in e.loc for e in sitemap.entries)


@pytest.mark.parametrize(
    ("url", "seccion"),
    [
        ("https://www.bancolombia.com/personas/cuentas/ahorros", "personas"),
        ("https://www.bancolombia.com/Empresas", "empresas"),
        ("https://www.bancolombia.com/wps/portal/negocios/x", "negocios"),
        ("https://www.bancolombia.com/", SECCION_RAIZ),
        ("https://www.bancolombia.com", SECCION_RAIZ),
        ("  https://www.bancolombia.com/centro-de-ayuda?x=1 ", "centro-de-ayuda"),
    ],
)
def test_section_of(url: str, seccion: str) -> None:
    """La sección es el primer segmento de la ruta (saltando /wps/portal)."""
    assert section_of(url) == seccion


def test_count_by_section_ordena_desc() -> None:
    """El conteo se ordena por frecuencia y luego por nombre."""
    urls = [
        "https://b.co/personas/a",
        "https://b.co/personas/b",
        "https://b.co/empresas/a",
        "https://b.co/acerca-de",
        "https://b.co/",
    ]

    assert list(count_by_section(urls).items()) == [
        ("personas", 2),
        (SECCION_RAIZ, 1),
        ("acerca-de", 1),
        ("empresas", 1),
    ]
