"""Utilidades de URL para el scraping."""

from pathlib import PurePosixPath
from urllib.parse import urlsplit


def extension_of(url: str) -> str:
    """Extensión del último segmento de la ruta, o `html (sin extensión)`."""
    sufijo = PurePosixPath(urlsplit(url).path).suffix.lower().lstrip(".")
    return sufijo or "html (sin extensión)"
