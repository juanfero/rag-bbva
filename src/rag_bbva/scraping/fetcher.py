"""Cliente HTTP cortés compartido por la exploración (M1) y el crawler (M2).

Garantiza en cada petición, incluidos los saltos de redirección: dominio objetivo,
reglas de `robots.txt`, User-Agent propio y pausa entre peticiones.
"""

import logging
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from rag_bbva.scraping.robots import RobotsTxt

logger = logging.getLogger(__name__)

MAX_REDIRECCIONES = 5


class FetchResult(BaseModel):
    """Resultado de una petición HTTP (o del motivo por el que no se hizo)."""

    url: str
    final_url: str | None = None
    status: int | None = None
    content_type: str | None = None
    size_bytes: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    skipped: str | None = None
    content: bytes = Field(default=b"", exclude=True, repr=False)

    @property
    def ok(self) -> bool:
        """La petición se hizo y devolvió 2xx."""
        return self.status is not None and 200 <= self.status < 300


class PoliteFetcher:
    """Cliente HTTP cortés: dominio acotado, `robots.txt`, User-Agent propio y pausa.

    La pausa efectiva entre peticiones es el máximo entre la configurada y el
    `Crawl-delay` de `robots.txt`.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        user_agent: str,
        allowed_host: str,
        delay_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Crea el fetcher sobre un `httpx.Client` ya configurado."""
        self._client = client
        self._user_agent = user_agent
        self._host = allowed_host.lower()
        self._delay = delay_seconds
        self._sleep = sleep
        self._clock = clock
        self._ultima: float | None = None
        self.robots: RobotsTxt | None = None
        self.requests_made = 0

    @property
    def delay_seconds(self) -> float:
        """Pausa efectiva entre peticiones."""
        robots_delay = self.robots.crawl_delay(self._user_agent) if self.robots else None
        return max(self._delay, robots_delay or 0.0)

    def is_allowed(self, url: str) -> bool:
        """URL dentro del dominio objetivo y permitida por `robots.txt`."""
        return self._motivo_bloqueo(url) is None

    def wait_turn(self) -> None:
        """Espera lo necesario para respetar la pausa desde la última petición."""
        if self._ultima is not None:
            restante = self.delay_seconds - (self._clock() - self._ultima)
            if restante > 0:
                self._sleep(restante)
        self._ultima = self._clock()

    def _motivo_bloqueo(self, url: str) -> str | None:
        """Motivo por el que no se puede pedir la URL, o `None` si está permitida."""
        if (urlsplit(url).hostname or "").lower() != self._host:
            return "fuera del dominio objetivo"
        if self.robots is not None and not self.robots.is_allowed(url, self._user_agent):
            return "prohibida por robots.txt"
        return None

    def fetch(self, url: str) -> FetchResult:
        """Descarga la URL si está permitida; nunca lanza por errores HTTP o de red.

        Las redirecciones se siguen a mano (máximo `MAX_REDIRECCIONES`) para validar
        cada salto contra el dominio y `robots.txt` y respetar la pausa en cada uno.
        """
        if motivo := self._motivo_bloqueo(url):
            return FetchResult(url=url, skipped=motivo)

        actual = url
        inicio = self._clock()
        for _ in range(MAX_REDIRECCIONES + 1):
            self.wait_turn()
            self.requests_made += 1
            try:
                respuesta = self._client.get(actual, follow_redirects=False)
            except httpx.HTTPError as exc:
                logger.warning("Fallo de red", extra={"url": actual, "error": repr(exc)})
                return FetchResult(url=url, final_url=actual, error=f"{type(exc).__name__}: {exc}")

            destino = respuesta.headers.get("location")
            if not (respuesta.is_redirect and destino):
                break
            siguiente = urljoin(actual, destino)
            if motivo := self._motivo_bloqueo(siguiente):
                return FetchResult(
                    url=url,
                    final_url=siguiente,
                    status=respuesta.status_code,
                    skipped=f"redirige a una URL {motivo}",
                )
            actual = siguiente
        else:
            return FetchResult(url=url, final_url=actual, error="demasiadas redirecciones")

        resultado = FetchResult(
            url=url,
            final_url=actual,
            status=respuesta.status_code,
            content_type=respuesta.headers.get("content-type"),
            size_bytes=len(respuesta.content),
            elapsed_ms=int((self._clock() - inicio) * 1000),
            content=respuesta.content,
        )
        logger.info(
            "Descarga",
            extra={"url": url, "status": resultado.status, "bytes": resultado.size_bytes},
        )
        return resultado
