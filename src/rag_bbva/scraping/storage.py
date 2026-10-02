"""Almacenamiento de datos crudos: HTML por página y manifest JSONL.

Estructura:
    <raw_dir>/pages/<sha1(url)>.html   HTML tal como lo sirvió el sitio
    <raw_dir>/manifest.jsonl           una línea por URL procesada

Las escrituras son atómicas (archivo temporal + `replace`). Un HTML cuyo hash no
cambió no se reescribe, lo que hace la re-ejecución incremental. El manifest se
fusiona con el anterior, para que un crawl parcial no borre lo descargado antes.
"""

import hashlib
import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from rag_bbva.exceptions import ScrapingError

logger = logging.getLogger(__name__)

Outcome = Literal[
    "guardada",  # HTML nuevo o modificado escrito en disco
    "sin_cambios",  # HTML idéntico al ya guardado: no se reescribe
    "duplicada",  # la URL final ya se guardó en este crawl (p. ej. tras redirección)
    "error_http",  # respuesta no 2xx (404, 403, 5xx tras reintentos…)
    "no_html",  # 2xx pero el contenido no es text/html
    "error_red",  # fallo de red o timeout tras reintentos, o demasiadas redirecciones
    "redireccion_omitida",  # redirige fuera del dominio o a una ruta prohibida
]


class ManifestEntry(BaseModel):
    """Registro de una URL procesada por el crawler."""

    url: str
    final_url: str | None = None
    status: int | None = None
    content_type: str | None = None
    outcome: Outcome
    fetched_at: str
    depth: int
    source: Literal["sitemap", "enlace"]
    lastmod: str | None = None
    content_hash: str | None = None
    size_bytes: int = 0
    elapsed_ms: int = 0
    attempts: int = 0
    path: str | None = None
    duplicate_of: str | None = None
    error: str | None = None


def sha256(content: bytes) -> str:
    """Hash SHA-256 en hexadecimal del contenido."""
    return hashlib.sha256(content).hexdigest()


def _escribir_atomico(destino: Path, datos: bytes) -> None:
    """Escribe en un temporal del mismo directorio y lo renombra (operación atómica)."""
    temporal = destino.with_name(f".{destino.name}.tmp")
    temporal.write_bytes(datos)
    os.replace(temporal, destino)


class RawStorage:
    """Repositorio en disco de las páginas crudas y su manifest."""

    def __init__(self, root: Path) -> None:
        """Usa (y crea si hace falta) `root/pages`."""
        self.root = root
        self.pages_dir = root / "pages"
        self.manifest_path = root / "manifest.jsonl"
        try:
            self.pages_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ScrapingError(
                "No se pudo crear el directorio de datos crudos", detail=str(exc)
            ) from exc

    def relative_path(self, url: str) -> str:
        """Ruta relativa a `root` del HTML de una URL: `pages/<sha1(url)>.html`."""
        return f"pages/{hashlib.sha1(url.encode('utf-8')).hexdigest()}.html"

    def save_page(self, url: str, content: bytes) -> tuple[str, str, bool]:
        """Guarda el HTML de la URL si cambió.

        Returns:
            (ruta relativa, hash SHA-256, `True` si se escribió en disco).
        """
        relativa = self.relative_path(url)
        destino = self.root / relativa
        nuevo_hash = sha256(content)
        if destino.exists() and sha256(destino.read_bytes()) == nuevo_hash:
            return relativa, nuevo_hash, False
        try:
            _escribir_atomico(destino, content)
        except OSError as exc:
            raise ScrapingError("No se pudo guardar el HTML", detail=f"{url}: {exc}") from exc
        return relativa, nuevo_hash, True

    def load_manifest(self) -> dict[str, ManifestEntry]:
        """Lee el manifest existente indexado por URL; líneas inválidas se ignoran."""
        if not self.manifest_path.exists():
            return {}
        entradas: dict[str, ManifestEntry] = {}
        for numero, linea in enumerate(self.manifest_path.read_text("utf-8").splitlines(), 1):
            if not linea.strip():
                continue
            try:
                entrada = ManifestEntry.model_validate_json(linea)
            except ValidationError:
                logger.warning("Línea inválida en el manifest", extra={"linea": numero})
                continue
            entradas[entrada.url] = entrada
        return entradas

    def write_manifest(self, entries: Iterable[ManifestEntry]) -> int:
        """Fusiona las entradas con el manifest previo (gana la nueva) y lo reescribe.

        Returns:
            Número total de entradas del manifest resultante.
        """
        fusion = self.load_manifest()
        for entrada in entries:
            fusion[entrada.url] = entrada
        lineas = "".join(
            json.dumps(e.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for e in fusion.values()
        )
        try:
            _escribir_atomico(self.manifest_path, lineas.encode("utf-8"))
        except OSError as exc:
            raise ScrapingError("No se pudo escribir el manifest", detail=str(exc)) from exc
        return len(fusion)
