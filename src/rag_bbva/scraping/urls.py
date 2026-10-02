"""Utilidades de URL para el scraping: normalización y filtros."""

from collections.abc import Iterable
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

# Parámetros de seguimiento que no cambian el contenido de la página.
_PARAMETROS_TRACKING = frozenset({"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl"})
_PUERTOS_DEFAULT = {"http": 80, "https": 443}
# Extensiones que pueden servir HTML; el resto (pdf, imágenes, sass…) se descarta.
EXTENSIONES_HTML = frozenset({"", "html", "htm", "aspx", "jsp", "php"})


def extension_of(url: str) -> str:
    """Extensión del último segmento de la ruta, o `html (sin extensión)`."""
    sufijo = PurePosixPath(urlsplit(url).path).suffix.lower().lstrip(".")
    return sufijo or "html (sin extensión)"


def _es_tracking(clave: str) -> bool:
    clave = clave.lower()
    return clave.startswith("utm_") or clave in _PARAMETROS_TRACKING


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Normaliza una URL para deduplicar y comparar.

    - Resuelve URLs relativas contra `base`.
    - Esquema y host en minúsculas; quita el puerto por defecto.
    - Quita el fragmento (`#…`) y los parámetros de tracking (`utm_*`, `gclid`…),
      conservando el orden del resto de la query.
    - Quita la barra final salvo en la raíz.

    Returns:
        La URL normalizada, o `None` si no es http(s) (mailto:, tel:, javascript:…).
    """
    url = url.strip()
    if base is not None:
        url = urljoin(base, url)
    partes = urlsplit(url)
    esquema = partes.scheme.lower()
    if esquema not in _PUERTOS_DEFAULT or not partes.hostname:
        return None

    host = partes.hostname.lower()
    if partes.port and partes.port != _PUERTOS_DEFAULT[esquema]:
        host = f"{host}:{partes.port}"
    ruta = partes.path or "/"
    if len(ruta) > 1:
        ruta = ruta.rstrip("/") or "/"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if not _es_tracking(k)]
    )
    return urlunsplit((esquema, host, ruta, query, ""))


def is_html_candidate(url: str) -> bool:
    """La extensión de la ruta puede corresponder a una página HTML."""
    sufijo = PurePosixPath(urlsplit(url).path).suffix.lower().lstrip(".")
    return sufijo in EXTENSIONES_HTML


def excluded_prefix(url: str, prefixes: Iterable[str]) -> str | None:
    """Primer prefijo de ruta que excluye la URL, o `None` si ninguno aplica.

    Un prefijo con barra final (`/acerca-de/sala-prensa/`) cubre también la ruta sin
    ella (`/acerca-de/sala-prensa`), porque `normalize_url` quita la barra final.
    """
    ruta = urlsplit(url).path or "/"
    for prefijo in prefixes:
        if ruta.startswith(prefijo) or (prefijo.endswith("/") and ruta == prefijo.rstrip("/")):
            return prefijo
    return None
