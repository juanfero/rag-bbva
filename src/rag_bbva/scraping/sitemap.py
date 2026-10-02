"""Parser de sitemaps XML (protocolo sitemaps.org) y utilidades de sección."""

import gzip
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from lxml import etree

from rag_bbva.exceptions import ScrapingError

_GZIP_MAGIC = b"\x1f\x8b"
# Prefijo de rutas de IBM WebSphere Portal, que usa el sitio objetivo (/wps/portal/<sección>/…).
_PREFIJO_PORTAL = ("wps", "portal")
SECCION_RAIZ = "(raiz)"


@dataclass(frozen=True)
class SitemapEntry:
    """Entrada de un sitemap: URL (`loc`) y fecha de modificación opcional."""

    loc: str
    lastmod: str | None = None


@dataclass(frozen=True)
class Sitemap:
    """Sitemap interpretado: índice de sitemaps o conjunto de URLs."""

    kind: Literal["index", "urlset"]
    entries: tuple[SitemapEntry, ...]


def _parser_seguro() -> etree.XMLParser:
    """Parser XML sin resolución de entidades ni acceso a red (evita XXE)."""
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def _hijo(elemento: etree._Element, nombre: str) -> str | None:
    """Texto del primer hijo con ese nombre local, sin importar el namespace."""
    for hijo in elemento:
        if isinstance(hijo.tag, str) and etree.QName(hijo).localname == nombre:
            texto = (hijo.text or "").strip()
            return texto or None
    return None


def parse_sitemap(content: bytes) -> Sitemap:
    """Interpreta un sitemap XML (opcionalmente comprimido con gzip).

    Raises:
        ScrapingError: si el contenido no es XML válido o no es un sitemap.
    """
    if content.startswith(_GZIP_MAGIC):
        try:
            content = gzip.decompress(content)
        except OSError as exc:
            raise ScrapingError("Sitemap gzip corrupto", detail=str(exc)) from exc

    try:
        raiz = etree.fromstring(content, parser=_parser_seguro())
    except etree.XMLSyntaxError as exc:
        raise ScrapingError("El sitemap no es XML válido", detail=str(exc)) from exc
    if raiz is None:
        raise ScrapingError("El sitemap está vacío")

    tipo = etree.QName(raiz).localname
    if tipo == "sitemapindex":
        kind: Literal["index", "urlset"] = "index"
        hijo_esperado = "sitemap"
    elif tipo == "urlset":
        kind = "urlset"
        hijo_esperado = "url"
    else:
        raise ScrapingError("Documento XML que no es un sitemap", detail=f"raíz <{tipo}>")

    entradas = []
    for nodo in raiz:
        if not isinstance(nodo.tag, str) or etree.QName(nodo).localname != hijo_esperado:
            continue
        loc = _hijo(nodo, "loc")
        if loc:
            entradas.append(SitemapEntry(loc=loc, lastmod=_hijo(nodo, "lastmod")))
    return Sitemap(kind=kind, entries=tuple(entradas))


def section_of(url: str) -> str:
    """Deriva la sección de una URL a partir del primer segmento de su ruta.

    Las rutas de WebSphere Portal (`/wps/portal/<sección>/…`) usan el segmento
    posterior al prefijo. La home se reporta como `(raiz)`.
    """
    segmentos = [s for s in urlsplit(url.strip()).path.lower().split("/") if s]
    if tuple(segmentos[:2]) == _PREFIJO_PORTAL:
        segmentos = segmentos[2:]
    return segmentos[0] if segmentos else SECCION_RAIZ


def count_by_section(urls: Iterable[str]) -> dict[str, int]:
    """Cuenta URLs por sección, ordenado de mayor a menor (empates por nombre)."""
    conteo = Counter(section_of(u) for u in urls)
    return dict(sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0])))


def interleave_by_section(urls: Iterable[str]) -> list[str]:
    """Reordena las URLs alternando secciones (round-robin), sin repetir.

    Conserva el orden relativo dentro de cada sección y recorre las secciones por
    orden de primera aparición. Así un crawl parcial cubre todas las secciones.
    """
    por_seccion: dict[str, list[str]] = {}
    for url in dict.fromkeys(urls):
        por_seccion.setdefault(section_of(url), []).append(url)
    colas = list(por_seccion.values())
    resultado: list[str] = []
    for i in range(max((len(c) for c in colas), default=0)):
        resultado.extend(c[i] for c in colas if i < len(c))
    return resultado
