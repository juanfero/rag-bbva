"""Esqueleto del crawl (patrón Template Method).

`BaseCrawler.crawl()` fija el algoritmo y sus garantías:
    prepare → discover_urls → [fetch → validate → persist → extract_links]* → manifest

Las subclases solo redefinen los pasos (cómo se descubren semillas, cómo se extraen
enlaces…). El esqueleto aplica en un único lugar el BFS acotado por `max_pages` y
`max_depth`, la deduplicación por URL normalizada y por URL final, y el corte ante
ráfagas de 403/429 (posible bloqueo del WAF).
"""

import logging
import time
from abc import ABC, abstractmethod
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from rag_bbva.scraping.fetcher import FetchResult, PoliteFetcher
from rag_bbva.scraping.storage import ManifestEntry, Outcome, RawStorage
from rag_bbva.scraping.urls import normalize_url

logger = logging.getLogger(__name__)

ESTADOS_DE_BLOQUEO = frozenset({403, 429})
_LOG_CADA = 25


@dataclass(frozen=True)
class CrawlTarget:
    """URL (normalizada) pendiente de descargar."""

    url: str
    depth: int
    source: Literal["sitemap", "enlace"]
    lastmod: str | None = None


class CrawlReport(BaseModel):
    """Resumen de una ejecución del crawler."""

    started_at: str
    finished_at: str
    duration_seconds: float
    seeds: int
    processed: int
    outcomes: dict[str, int]
    status_codes: dict[str, int]
    skipped: dict[str, int]
    links_enqueued: int
    requests_made: int
    manifest_entries: int
    aborted: bool = False
    abort_reason: str | None = None


class BlockGuard:
    """Detecta ráfagas de respuestas 403/429 consecutivas."""

    def __init__(self, threshold: int) -> None:
        """`threshold` respuestas de bloqueo seguidas disparan el corte."""
        self._threshold = threshold
        self.consecutive = 0

    def record(self, status: int | None) -> bool:
        """Registra un estado y devuelve `True` si se alcanzó el umbral.

        Los fallos de red (`None`) no cuentan ni reinician la racha.
        """
        if status in ESTADOS_DE_BLOQUEO:
            self.consecutive += 1
        elif status is not None:
            self.consecutive = 0
        return self.consecutive >= self._threshold


def _ahora() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


class BaseCrawler(ABC):
    """Crawler genérico. `crawl()` es el método plantilla y no debe redefinirse."""

    def __init__(
        self,
        fetcher: PoliteFetcher,
        storage: RawStorage,
        *,
        max_pages: int,
        max_depth: int,
        block_threshold: int,
    ) -> None:
        """Configura límites y dependencias (fetcher y almacenamiento inyectados)."""
        self.fetcher = fetcher
        self.storage = storage
        self.max_pages = max_pages
        self.max_depth = max_depth
        self._guard = BlockGuard(block_threshold)
        self._finales: dict[str, str] = {}
        self.skipped: Counter[str] = Counter()

    # ------------------------------------------------------------------ plantilla
    def crawl(self) -> CrawlReport:
        """Ejecuta el crawl completo y escribe el manifest (también si se aborta)."""
        inicio, t0 = _ahora(), time.perf_counter()
        self.prepare()
        semillas = list(self.discover_urls())
        frontera: deque[CrawlTarget] = deque(semillas)
        encoladas = {t.url for t in semillas}
        entradas: list[ManifestEntry] = []
        enlaces = 0
        abortado: str | None = None

        while frontera and len(entradas) < self.max_pages:
            objetivo = frontera.popleft()
            if objetivo.url in self._finales:
                self.skipped["ya descargada vía redirección"] += 1
                continue
            if not self.fetcher.is_allowed(objetivo.url):
                self.skipped["prohibida por robots.txt o fuera del dominio"] += 1
                continue

            resultado = self.fetch(objetivo)
            outcome = self.validate(objetivo, resultado)
            entrada = self.persist(objetivo, resultado, outcome)
            entradas.append(entrada)
            if len(entradas) % _LOG_CADA == 0:
                logger.info("Progreso del crawl", extra={"procesadas": len(entradas)})

            if self._guard.record(resultado.status):
                abortado = (
                    f"{self._guard.consecutive} respuestas 403/429 consecutivas "
                    "(posible bloqueo del WAF)"
                )
                logger.error("Crawl abortado", extra={"motivo": abortado, "url": objetivo.url})
                break

            if entrada.outcome in {"guardada", "sin_cambios"} and objetivo.depth < self.max_depth:
                for enlace in self.extract_links(objetivo, resultado):
                    if enlace not in encoladas:
                        encoladas.add(enlace)
                        frontera.append(CrawlTarget(enlace, objetivo.depth + 1, "enlace"))
                        enlaces += 1

        total = self.storage.write_manifest(entradas)
        return CrawlReport(
            started_at=inicio,
            finished_at=_ahora(),
            duration_seconds=round(time.perf_counter() - t0, 1),
            seeds=len(semillas),
            processed=len(entradas),
            outcomes=dict(Counter(e.outcome for e in entradas)),
            status_codes=dict(Counter(str(e.status) for e in entradas)),
            skipped=dict(self.skipped),
            links_enqueued=enlaces,
            requests_made=self.fetcher.requests_made,
            manifest_entries=total,
            aborted=abortado is not None,
            abort_reason=abortado,
        )

    # ------------------------------------------------------------------ pasos
    def prepare(self) -> None:  # noqa: B027 - gancho opcional, vacío a propósito
        """Gancho previo al descubrimiento (p. ej. leer robots.txt)."""

    @abstractmethod
    def discover_urls(self) -> Iterable[CrawlTarget]:
        """Devuelve las semillas del crawl (profundidad 0), ya normalizadas."""

    def fetch(self, target: CrawlTarget) -> FetchResult:
        """Descarga la URL con el fetcher cortés."""
        return self.fetcher.fetch(target.url)

    def validate(self, target: CrawlTarget, result: FetchResult) -> Outcome:
        """Clasifica la respuesta. `guardada` significa "HTML válido para persistir"."""
        if result.skipped:
            return "redireccion_omitida"
        if result.error:
            return "error_red"
        if not result.ok:
            return "error_http"
        if "text/html" not in (result.content_type or "").lower():
            return "no_html"
        return "guardada"

    def persist(self, target: CrawlTarget, result: FetchResult, outcome: Outcome) -> ManifestEntry:
        """Guarda el HTML válido (si no es duplicado) y construye la entrada del manifest."""
        final = normalize_url(result.final_url) if result.final_url else None
        entrada = ManifestEntry(
            url=target.url,
            final_url=final,
            status=result.status,
            content_type=result.content_type,
            outcome=outcome,
            fetched_at=_ahora(),
            depth=target.depth,
            source=target.source,
            lastmod=target.lastmod,
            size_bytes=result.size_bytes,
            elapsed_ms=result.elapsed_ms,
            attempts=result.attempts,
            error=result.error or result.skipped,
        )
        if outcome != "guardada":
            return entrada

        clave = final or target.url
        if clave in self._finales:
            entrada.outcome = "duplicada"
            entrada.duplicate_of = self._finales[clave]
            return entrada
        self._finales[clave] = target.url
        ruta, hash_, escrito = self.storage.save_page(clave, result.content)
        entrada.path, entrada.content_hash = ruta, hash_
        entrada.outcome = "guardada" if escrito else "sin_cambios"
        return entrada

    def extract_links(self, target: CrawlTarget, result: FetchResult) -> Iterable[str]:
        """Enlaces a encolar desde una página (por defecto, ninguno)."""
        return ()
