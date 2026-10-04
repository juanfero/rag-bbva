"""Pipeline de limpieza (M3): `data/raw/` → `data/clean/documents.jsonl`.

`CleaningPipeline` arma la cadena de pasos (Chain of Responsibility) y la aplica a
cada página cruda del manifest de M2. El resultado es determinista: las páginas se
procesan en un orden fijo y ningún campo depende de la hora de ejecución.
"""

import hashlib
import json
import logging
import os
import statistics
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from rag_bbva.exceptions import ProcessingError
from rag_bbva.processing.models import CleanDocument, Discarded, RawPage, WorkingDocument
from rag_bbva.processing.quality import LeakReport, TableReport, leak_report, table_report
from rag_bbva.processing.steps import (
    CleaningStep,
    DeduplicateStep,
    DetectLanguageStep,
    ExtractMainContentStep,
    ExtractMetadataStep,
    MinLengthFilterStep,
    NormalizeTextStep,
    ParseHtmlStep,
    RemoveBoilerplateStep,
    primary_language,
)
from rag_bbva.scraping.sitemap import section_of
from rag_bbva.scraping.storage import read_manifest

logger = logging.getLogger(__name__)


@dataclass
class ManifestReadResult:
    """Páginas a limpiar y entradas del manifest que no se leen (con motivo)."""

    pages: list[RawPage] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)


def read_raw_pages(raw_dir: Path) -> ManifestReadResult:
    """Lee del manifest solo las entradas con HTML guardado.

    - Sin `path` (errores, redirecciones omitidas, excluidas…) o `duplicada`: no se leen.
    - La URL canónica es `final_url`; si dos entradas comparten URL final se lee una.
    - Orden fijo: por longitud y luego alfabético de la URL canónica, para que la
      deduplicación por texto conserve la URL más corta y el resultado sea determinista.
    """
    ruta_manifest = raw_dir / "manifest.jsonl"
    manifest = read_manifest(ruta_manifest)
    if not manifest:
        raise ProcessingError(
            "No hay manifest de datos crudos; ejecute antes `scrape`", detail=str(ruta_manifest)
        )

    resultado = ManifestReadResult()
    por_url: dict[str, RawPage] = {}
    for entrada in manifest.values():
        if entrada.outcome == "duplicada":
            resultado.skipped["duplicada"] += 1
            continue
        if entrada.path is None:
            resultado.skipped[f"sin_html ({entrada.outcome})"] += 1
            continue
        url = entrada.final_url or entrada.url
        if url in por_url:
            resultado.skipped["misma_url_final"] += 1
            continue
        por_url[url] = RawPage(
            url=url,
            source_url=entrada.url,
            path=entrada.path,
            lastmod=entrada.lastmod,
            fetched_at=entrada.fetched_at,
        )
    resultado.pages = sorted(por_url.values(), key=lambda p: (len(p.url), p.url))
    return resultado


def _cargar_html(raw_dir: Path, page: RawPage) -> RawPage | None:
    ruta = raw_dir / page.path
    try:
        contenido = ruta.read_bytes()
    except OSError:
        return None
    return page.model_copy(update={"html": contenido.decode("utf-8", errors="replace")})


class Distribution(BaseModel):
    """Distribución de longitudes de los documentos conservados."""

    min: int
    p50: int
    p95: int
    max: int


class DiscardedDocument(BaseModel):
    """Documento descartado y su motivo."""

    url: str
    reason: str
    detail: str | None = None


class CleanReport(BaseModel):
    """Resumen de la limpieza (`data/clean/clean_report.json`)."""

    manifest_skipped: dict[str, int]
    processed: int
    kept: int
    discarded: dict[str, int]
    discarded_documents: list[DiscardedDocument]
    n_chars: Distribution | None
    by_section: dict[str, int]
    by_template: dict[str, int]
    by_extraction: dict[str, int]
    by_lang: dict[str, int]
    # Documentos cuyo idioma detectado difiere del declarado en `<html lang>`
    # (comparando la subetiqueta principal: `es-CO` cuenta como `es`).
    lang_mismatch: int
    lang_mismatch_pairs: dict[str, int]
    # Documentos sin señal clara de idioma (lang tomado de <html lang>), por plantilla
    # y por idioma resultante.
    lang_fallback_by_template: dict[str, dict[str, int]]
    leaks: LeakReport
    # Tablas markdown y filas con un número de celdas distinto al del encabezado (M10).
    tables: TableReport


def _percentil(valores: Sequence[int], q: float) -> int:
    """Percentil por el método del rango más cercano (determinista y sin interpolar)."""
    ordenados = sorted(valores)
    indice = max(0, min(len(ordenados) - 1, round(q * len(ordenados) + 0.5) - 1))
    return ordenados[indice]


def _ordenado(conteo: Counter[str]) -> dict[str, int]:
    return dict(sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0])))


def _respaldo_por_plantilla(documentos: Sequence[CleanDocument]) -> dict[str, dict[str, int]]:
    """{plantilla: {idioma: n}} de los documentos cuyo `lang` vino de `<html lang>`."""
    por_plantilla: dict[str, Counter[str]] = {}
    for d in documentos:
        if d.lang_source == "html_lang":
            por_plantilla.setdefault(d.template, Counter())[d.lang or "desconocido"] += 1
    return {p: _ordenado(c) for p, c in sorted(por_plantilla.items())}


@dataclass
class CleaningResult:
    """Documentos limpios y reporte de una ejecución."""

    documents: list[CleanDocument]
    report: CleanReport


class CleaningPipeline:
    """Cadena de limpieza configurable: cada paso se puede probar y sustituir aislado."""

    def __init__(self, steps: Sequence[CleaningStep]) -> None:
        """Enlaza los pasos en el orden dado (el primero recibe cada documento)."""
        if not steps:
            raise ProcessingError("El pipeline de limpieza necesita al menos un paso")
        self.steps = list(steps)
        for actual, siguiente in zip(self.steps, self.steps[1:], strict=False):
            actual.set_next(siguiente)

    @classmethod
    def default(cls, *, min_chars: int, min_extraction_coverage: float) -> "CleaningPipeline":
        """Cadena estándar de M3."""
        return cls(
            [
                ParseHtmlStep(),
                ExtractMetadataStep(),
                RemoveBoilerplateStep(),
                ExtractMainContentStep(min_coverage=min_extraction_coverage),
                NormalizeTextStep(),
                DetectLanguageStep(),
                MinLengthFilterStep(min_chars=min_chars),
                DeduplicateStep(),
            ]
        )

    def clean(self, page: RawPage) -> CleanDocument | Discarded:
        """Pasa una página por la cadena y construye el documento limpio."""
        resultado = self.steps[0].handle(WorkingDocument(page=page))
        if isinstance(resultado, Discarded):
            return resultado
        return CleanDocument(
            doc_id=hashlib.sha1(page.url.encode("utf-8")).hexdigest(),
            url=page.url,
            title=resultado.title,
            section=section_of(page.url),
            breadcrumbs=resultado.breadcrumbs,
            text=resultado.text,
            html_lang=resultado.html_lang,
            lang=resultado.lang,
            lang_source=resultado.lang_source,
            lastmod=page.lastmod,
            published_at=resultado.published_at,
            scraped_at=page.fetched_at,
            content_hash=resultado.content_hash,
            n_chars=len(resultado.text),
            template=resultado.template,
            extraction=resultado.extraction,
        )

    def run(
        self, pages: Iterable[RawPage], raw_dir: Path
    ) -> Iterator[CleanDocument | DiscardedDocument]:
        """Limpia las páginas en orden; las que no se pueden leer se descartan."""
        for paso in self.steps:
            paso.reset()
        for page in pages:
            cargada = _cargar_html(raw_dir, page)
            if cargada is None:
                yield DiscardedDocument(url=page.url, reason="html_no_encontrado", detail=page.path)
                continue
            resultado = self.clean(cargada)
            if isinstance(resultado, Discarded):
                yield DiscardedDocument(
                    url=page.url, reason=resultado.reason, detail=resultado.detail
                )
            else:
                yield resultado

    def process_directory(self, raw_dir: Path) -> CleaningResult:
        """Lee el manifest de `raw_dir`, limpia todas las páginas y arma el reporte."""
        lectura = read_raw_pages(raw_dir)
        documentos: list[CleanDocument] = []
        descartados: list[DiscardedDocument] = []
        for item in self.run(lectura.pages, raw_dir):
            (documentos if isinstance(item, CleanDocument) else descartados).append(item)
        logger.info(
            "Limpieza terminada",
            extra={"procesados": len(lectura.pages), "conservados": len(documentos)},
        )
        longitudes = [d.n_chars for d in documentos]
        distintos = Counter(
            f"{d.html_lang} → {d.lang}"
            for d in documentos
            if d.html_lang and primary_language(d.html_lang) != d.lang
        )
        reporte = CleanReport(
            manifest_skipped=_ordenado(lectura.skipped),
            processed=len(lectura.pages),
            kept=len(documentos),
            discarded=_ordenado(Counter(d.reason for d in descartados)),
            discarded_documents=descartados,
            n_chars=Distribution(
                min=min(longitudes),
                p50=int(statistics.median_low(longitudes)),
                p95=_percentil(longitudes, 0.95),
                max=max(longitudes),
            )
            if longitudes
            else None,
            by_section=_ordenado(Counter(d.section for d in documentos)),
            by_template=_ordenado(Counter(d.template for d in documentos)),
            by_extraction=_ordenado(Counter(d.extraction for d in documentos)),
            by_lang=_ordenado(Counter(d.lang or "desconocido" for d in documentos)),
            lang_mismatch=sum(distintos.values()),
            lang_mismatch_pairs=_ordenado(distintos),
            lang_fallback_by_template=_respaldo_por_plantilla(documentos),
            leaks=leak_report(documentos),
            tables=table_report(documentos),
        )
        return CleaningResult(documentos, reporte)


def _escribir_atomico(destino: Path, texto: str) -> None:
    temporal = destino.with_name(f".{destino.name}.tmp")
    temporal.write_text(texto, encoding="utf-8")
    os.replace(temporal, destino)


def write_clean_output(result: CleaningResult, clean_dir: Path) -> tuple[Path, Path]:
    """Escribe `documents.jsonl` y `clean_report.json` de forma atómica."""
    try:
        clean_dir.mkdir(parents=True, exist_ok=True)
        documentos = clean_dir / "documents.jsonl"
        reporte = clean_dir / "clean_report.json"
        _escribir_atomico(
            documentos,
            "".join(
                json.dumps(d.model_dump(mode="json"), ensure_ascii=False) + "\n"
                for d in result.documents
            ),
        )
        _escribir_atomico(
            reporte,
            json.dumps(result.report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        )
    except OSError as exc:
        raise ProcessingError("No se pudo escribir la salida limpia", detail=str(exc)) from exc
    return documentos, reporte
