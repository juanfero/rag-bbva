"""Pruebas de reintentos y backoff del PoliteFetcher con respx (M2, sin red)."""

import httpx
import pytest
import respx

from rag_bbva.scraping.fetcher import PoliteFetcher

URL = "https://www.banco.test/personas"
UA = "RAG-BBVA-TechTest/1.0"


class Reloj:
    """Reloj falso: `sleep` avanza el tiempo y registra las esperas."""

    def __init__(self) -> None:
        self.ahora = 0.0
        self.esperas: list[float] = []

    def clock(self) -> float:
        return self.ahora

    def sleep(self, segundos: float) -> None:
        self.esperas.append(round(segundos, 3))
        self.ahora += segundos


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


def _fetcher(reloj: Reloj, max_retries: int = 3, delay: float = 0.0) -> PoliteFetcher:
    return PoliteFetcher(
        httpx.Client(headers={"User-Agent": UA}),
        user_agent=UA,
        allowed_host="www.banco.test",
        delay_seconds=delay,
        max_retries=max_retries,
        backoff_seconds=2.0,
        sleep=reloj.sleep,
        clock=reloj.clock,
    )


@respx.mock
def test_503_se_reintenta_con_backoff_y_se_recupera(reloj: Reloj) -> None:
    """Dos 503 y luego 200: se reintenta esperando 2 s y 4 s."""
    ruta = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, text="ok")]
    )

    resultado = _fetcher(reloj).fetch(URL)

    assert resultado.ok
    assert resultado.attempts == 3
    assert ruta.call_count == 3
    assert reloj.esperas == [2.0, 4.0]


@respx.mock
def test_503_persistente_se_rinde_sin_lanzar(reloj: Reloj) -> None:
    """Tras 1 + N intentos devuelve el último 503 como resultado, sin excepción."""
    ruta = respx.get(URL).mock(return_value=httpx.Response(503))

    resultado = _fetcher(reloj, max_retries=2).fetch(URL)

    assert resultado.status == 503
    assert not resultado.ok
    assert resultado.attempts == 3
    assert ruta.call_count == 3
    assert reloj.esperas == [2.0, 4.0]


@respx.mock
def test_timeout_se_reintenta(reloj: Reloj) -> None:
    """Un timeout es transitorio: se reintenta y la segunda petición funciona."""
    respx.get(URL).mock(side_effect=[httpx.ReadTimeout("lento"), httpx.Response(200, text="ok")])

    resultado = _fetcher(reloj).fetch(URL)

    assert resultado.ok
    assert resultado.attempts == 2


@respx.mock
def test_fallo_de_red_persistente_devuelve_error(reloj: Reloj) -> None:
    """Si la red falla en todos los intentos el resultado lleva el error, sin lanzar."""
    respx.get(URL).mock(side_effect=httpx.ConnectError("caído"))

    resultado = _fetcher(reloj, max_retries=1).fetch(URL)

    assert resultado.status is None
    assert resultado.error is not None
    assert resultado.error.startswith("ConnectError")
    assert resultado.attempts == 2


@pytest.mark.parametrize("estado", [403, 404, 429])
@respx.mock
def test_errores_no_transitorios_no_se_reintentan(reloj: Reloj, estado: int) -> None:
    """403, 404 y 429 se devuelven al primer intento, sin backoff."""
    ruta = respx.get(URL).mock(return_value=httpx.Response(estado))

    resultado = _fetcher(reloj).fetch(URL)

    assert resultado.status == estado
    assert resultado.attempts == 1
    assert ruta.call_count == 1
    assert reloj.esperas == []


@respx.mock
def test_sin_reintentos_por_defecto(reloj: Reloj) -> None:
    """Con max_retries=0 (comportamiento de M1) un 503 no se reintenta."""
    ruta = respx.get(URL).mock(return_value=httpx.Response(503))

    resultado = _fetcher(reloj, max_retries=0).fetch(URL)

    assert resultado.status == 503
    assert ruta.call_count == 1


@respx.mock
def test_reintentos_respetan_la_pausa_entre_peticiones(reloj: Reloj) -> None:
    """Con pausa de 5 s y backoff de 2 s, cada reintento espera al menos 5 s."""
    respx.get(URL).mock(side_effect=[httpx.Response(502), httpx.Response(200)])

    _fetcher(reloj, delay=5.0).fetch(URL)

    assert reloj.esperas == [2.0, 3.0]
    assert reloj.ahora == 5.0
