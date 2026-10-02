"""Modelos de la limpieza (M3): página cruda de entrada, documento en proceso y salida."""

from dataclasses import dataclass, field
from typing import Literal

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

# Plantillas del sitio identificadas en M1 (`docs/exploracion_sitio.md` §5).
Template = Literal["A_main", "B_main_content", "C_role_main", "otra"]
Extraction = Literal["trafilatura", "selector"]


class RawPage(BaseModel):
    """Página cruda leída del manifest de M2."""

    url: str  # URL canónica: `final_url` del manifest
    source_url: str  # URL pedida (clave del manifest)
    path: str
    lastmod: str | None = None
    fetched_at: str
    html: str = Field(default="", repr=False)


class CleanDocument(BaseModel):
    """Documento limpio, una línea de `data/clean/documents.jsonl`."""

    doc_id: str
    url: str
    title: str | None
    section: str
    breadcrumbs: list[str]
    text: str
    html_lang: str | None  # valor de `<html lang>` tal como lo declara el sitio
    lang: str | None  # idioma del contenido (detectado; si no hay señal, el de html_lang)
    lastmod: str | None
    published_at: str | None
    scraped_at: str
    content_hash: str
    n_chars: int
    template: Template
    extraction: Extraction


@dataclass
class Discarded:
    """Resultado de un paso que descarta el documento, con el motivo."""

    reason: str
    detail: str | None = None


@dataclass
class WorkingDocument:
    """Estado mutable de un documento mientras recorre la cadena de limpieza."""

    page: RawPage
    soup: BeautifulSoup | None = None
    container: Tag | None = None
    title: str | None = None
    html_lang: str | None = None
    lang: str | None = None
    published_at: str | None = None
    breadcrumbs: list[str] = field(default_factory=list)
    template: Template = "otra"
    extraction: Extraction = "selector"
    text: str = ""
    content_hash: str = ""
