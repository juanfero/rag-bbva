"""Latencia del LLM (M10, ADR-017): timeout por llamada, respaldo ante timeout/5xx/429
tras un reintento del principal y presupuesto total del turno. Sin red: API simulada."""

import json
import time
from collections.abc import Callable, Sequence

import httpx2
import pytest

from rag_bbva.config import Settings
from rag_bbva.exceptions import (
    LLMBudgetExceededError,
    LLMError,
    LLMQuotaError,
    LLMUnavailableError,
)
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.llm import budget
from rag_bbva.llm.provider import (
    FakeLLMProvider,
    FallbackLLMProvider,
    GeminiProvider,
    LLMResponse,
    Message,
)
from rag_bbva.memory.repository import InMemoryConversationRepository

from .fakes_rag import LLMGuionado, RetrieverFalso, ajustes, servicio

MENSAJES = [{"role": "user", "content": "hola"}]
PRINCIPAL, RESPALDO = "gemini-2.5-flash", "gemini-3.1-flash-lite"


def _completado(modelo: str) -> dict[str, object]:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": modelo,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": f"responde {modelo}"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


class Api:
    """API simulada: el modelo principal responde con `falla`; el respaldo, 200."""

    def __init__(self, falla: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.falla = falla
        self.peticiones: list[tuple[str, dict[str, float] | None]] = []

    def __call__(self, peticion: httpx2.Request) -> httpx2.Response:
        modelo = json.loads(peticion.content)["model"]
        self.peticiones.append((modelo, peticion.extensions.get("timeout")))
        if modelo == PRINCIPAL:
            return self.falla(peticion)
        return httpx2.Response(200, json=_completado(modelo))

    def modelos(self) -> list[str]:
        return [m for m, _ in self.peticiones]


def _gemini(
    api: Api, modelo: str, max_retries: int, esperas: list[float] | None = None
) -> GeminiProvider:
    return GeminiProvider(
        api_key="clave-de-prueba",
        base_url="http://gemini-falso/v1",
        model=modelo,
        max_retries=max_retries,
        backoff_seconds=1.0,
        timeout=20,
        http_client=httpx2.Client(transport=httpx2.MockTransport(api)),
        sleep=(esperas.append if esperas is not None else lambda _s: None),
    )


def _con_respaldo(api: Api) -> FallbackLLMProvider:
    """Como lo arma la fábrica: el principal con UN reintento."""
    return FallbackLLMProvider(_gemini(api, PRINCIPAL, 1), _gemini(api, RESPALDO, 2))


def _timeout(_peticion: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("el modelo no respondió a tiempo")


@pytest.mark.parametrize(
    ("nombre", "falla", "pedidos_al_principal"),
    [
        ("timeout", _timeout, 2),
        ("503", lambda _p: httpx2.Response(503, json={"error": {"message": "high demand"}}), 2),
        ("500", lambda _p: httpx2.Response(500, json={"error": {"message": "internal"}}), 2),
        ("429 por minuto", lambda _p: httpx2.Response(429, json={"error": {"message": "rate"}}), 2),
        (
            "429 cupo diario",
            lambda _p: httpx2.Response(
                429,
                json=[
                    {
                        "error": {
                            "code": 429,
                            "message": "Quota exceeded",
                            "details": [
                                {
                                    "violations": [
                                        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}
                                    ]
                                }
                            ],
                        }
                    }
                ],
            ),
            1,  # el cupo diario no se reintenta
        ),
    ],
)
def test_respaldo_tras_un_reintento_del_principal(
    nombre: str, falla: Callable[[httpx2.Request], httpx2.Response], pedidos_al_principal: int
) -> None:
    api = Api(falla)
    respuesta = _con_respaldo(api).complete(MENSAJES)
    assert respuesta.model == RESPALDO, nombre
    assert api.modelos() == [PRINCIPAL] * pedidos_al_principal + [RESPALDO], nombre


@pytest.mark.parametrize(
    ("codigo", "cuerpo"),
    [
        (401, {"error": {"message": "API key not valid"}}),
        (400, {"error": {"message": "API key not valid. Please pass a valid API key."}}),
        (404, {"error": {"message": "model not found"}}),
    ],
)
def test_clave_o_modelo_invalidos_no_activan_el_respaldo(
    codigo: int, cuerpo: dict[str, object]
) -> None:
    api = Api(lambda _p: httpx2.Response(codigo, json=cuerpo))
    with pytest.raises(LLMError) as error:
        _con_respaldo(api).complete(MENSAJES)
    assert type(error.value) is LLMError
    assert api.modelos() == [PRINCIPAL]  # ni reintento ni respaldo


def test_clasificacion_de_errores_del_proveedor() -> None:
    casos = {503: LLMUnavailableError, 429: LLMQuotaError, 401: LLMError, 404: LLMError}
    for codigo, clase in casos.items():
        api = Api(lambda _p, c=codigo: httpx2.Response(c, json={"error": {"message": "x"}}))
        with pytest.raises(LLMError) as error:
            _gemini(api, PRINCIPAL, 0).complete(MENSAJES)
        assert type(error.value) is clase, codigo
    with pytest.raises(LLMUnavailableError):
        _gemini(Api(_timeout), PRINCIPAL, 0).complete(MENSAJES)


# --- Timeout por llamada y presupuesto del turno -------------------------------------------


def test_timeout_por_llamada_es_el_configurado_o_el_plazo_restante() -> None:
    api = Api(lambda _p: httpx2.Response(200, json=_completado(PRINCIPAL)))
    proveedor = _gemini(api, PRINCIPAL, 0)

    proveedor.complete(MENSAJES)
    with budget.turn_budget(5):
        proveedor.complete(MENSAJES)

    sin_plazo, con_plazo = (t["read"] for _, t in api.peticiones)  # type: ignore[index]
    assert sin_plazo == 20  # LLM_TIMEOUT_SECONDS
    assert 4 < con_plazo <= 5  # recortado al plazo restante del turno


def test_sin_plazo_no_se_llama_al_llm() -> None:
    api = Api(lambda _p: httpx2.Response(200, json=_completado(PRINCIPAL)))
    with budget.turn_budget(0.001):
        time.sleep(0.01)
        with pytest.raises(LLMBudgetExceededError, match="lento"):
            _gemini(api, PRINCIPAL, 2).complete(MENSAJES)
    assert api.peticiones == []


def test_las_esperas_entre_reintentos_no_pasan_del_plazo() -> None:
    esperas: list[float] = []
    api = Api(lambda _p: httpx2.Response(503, json={"error": {"message": "x"}}))
    with budget.turn_budget(0.3), pytest.raises(LLMError):
        _gemini(api, PRINCIPAL, 3, esperas).complete(MENSAJES)
    assert esperas and all(e <= 0.3 for e in esperas)  # el backoff base sería 1, 2, 4 s


class Lento(FakeLLMProvider):
    """Tarda `segundos` y luego falla como un modelo caído (o responde)."""

    def __init__(self, segundos: float, falla: bool = True) -> None:
        super().__init__("respuesta lenta [1].", model="lento")
        self.segundos, self.falla = segundos, falla

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int | None = None
    ) -> LLMResponse:
        self.calls.append(list(messages))
        time.sleep(self.segundos)
        if self.falla:
            raise LLMUnavailableError("El servicio de respuestas tardó demasiado en contestar.")
        return super().complete(messages, max_tokens=max_tokens)


def test_respaldo_no_se_intenta_si_ya_no_queda_plazo() -> None:
    respaldo = FakeLLMProvider(model=RESPALDO)
    with budget.turn_budget(0.05), pytest.raises(LLMBudgetExceededError):
        FallbackLLMProvider(Lento(0.1), respaldo).complete(MENSAJES)
    assert respaldo.calls == []


def test_turno_que_excede_el_presupuesto_da_error_amigable_y_no_se_guarda() -> None:
    repo = InMemoryConversationRepository()
    rag = servicio(
        settings=ajustes(llm_turn_budget_seconds=0.1),
        repo=repo,
        llm=Lento(0.2),
        retriever=RetrieverFalso(),
    )  # type: ignore[arg-type]

    with pytest.raises(LLMBudgetExceededError) as error:
        rag.ask(None, "¿Qué es un CDT?")

    assert error.value.message == budget.MENSAJE_LENTO
    assert repo.list_conversations() == []


def test_turno_dentro_del_presupuesto_responde() -> None:
    rag = servicio(settings=ajustes(llm_turn_budget_seconds=5), llm=LLMGuionado())
    assert rag.ask(None, "¿Qué es un CDT?").no_answer is False


def test_api_responde_503_lento_y_no_guarda(tmp_path: object) -> None:
    from fastapi.testclient import TestClient

    from rag_bbva.api.app import create_app

    config = ajustes(llm_turn_budget_seconds=0.1)
    repo = InMemoryConversationRepository()
    rag = servicio(settings=config, repo=repo, llm=Lento(0.2), retriever=RetrieverFalso())  # type: ignore[arg-type]
    app = create_app(
        config, service=rag, health_checker=ComponentFactory(config).create_health_checker(rag)
    )
    with TestClient(app) as cliente:
        r = cliente.post("/chat", json={"question": "¿Qué es un CDT?"})
        conversaciones = cliente.get("/conversations").json()
    assert r.status_code == 503
    assert r.json() == {"error": budget.MENSAJE_LENTO, "detail": None}
    assert conversaciones == []


def test_fabrica_da_un_reintento_al_principal_con_respaldo(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GEMINI_API_KEY", "clave-de-prueba")
    clean_env.setenv("LLM_MAX_RETRIES", "3")
    llm = ComponentFactory(Settings(_env_file=None)).create_llm()  # type: ignore[call-arg]
    assert isinstance(llm, FallbackLLMProvider)
    assert llm.primary.max_retries == 1 and llm.fallback.max_retries == 3  # type: ignore[attr-defined]
    assert llm.primary.timeout == 20  # type: ignore[attr-defined]

    clean_env.setenv("LLM_FALLBACK_MODEL", "")
    sin_respaldo = ComponentFactory(Settings(_env_file=None)).create_llm()  # type: ignore[call-arg]
    assert sin_respaldo.max_retries == 3  # type: ignore[attr-defined]
    rag = ComponentFactory(Settings(_env_file=None, llm_provider="fake")).create_rag_service(  # type: ignore[call-arg]
        repository=InMemoryConversationRepository(), retriever=RetrieverFalso()
    )
    assert rag.turn_budget_seconds == 45


# --- Tope de reloj por llamada (M10): el timeout de httpx es por lectura, no total --------


def _lento_sin_cortar(segundos: float) -> Callable[[httpx2.Request], httpx2.Response]:
    """Servidor que contesta tarde sin que httpx lo corte (como el 503 de 39 s de M10)."""

    def responder(_peticion: httpx2.Request) -> httpx2.Response:
        time.sleep(segundos)
        return httpx2.Response(503, json={"error": {"message": "high demand"}})

    return responder


def _gemini_rapido(api: Api, modelo: str, max_retries: int, timeout: float) -> GeminiProvider:
    return GeminiProvider(
        api_key="clave-de-prueba",
        base_url="http://gemini-falso/v1",
        model=modelo,
        max_retries=max_retries,
        backoff_seconds=0.01,
        timeout=timeout,
        http_client=httpx2.Client(transport=httpx2.MockTransport(api)),
        sleep=lambda _s: None,
    )


def test_tope_de_reloj_corta_la_llamada_lenta_y_pasa_al_respaldo() -> None:
    api = Api(_lento_sin_cortar(2.0))
    llm = FallbackLLMProvider(
        _gemini_rapido(api, PRINCIPAL, 1, timeout=0.2),
        _gemini_rapido(api, RESPALDO, 1, timeout=0.2),
    )

    inicio = time.perf_counter()
    respuesta = llm.complete(MENSAJES)
    duracion = time.perf_counter() - inicio

    assert respuesta.model == RESPALDO
    assert duracion < 1.0  # 2 intentos de 0,2 s + respaldo, no dos esperas de 2 s
    assert api.modelos()[:2] == [PRINCIPAL, PRINCIPAL]


def test_tope_de_reloj_sin_respaldo_da_error_de_timeout() -> None:
    api = Api(_lento_sin_cortar(2.0))
    inicio = time.perf_counter()
    with pytest.raises(LLMUnavailableError, match="tardó demasiado"):
        _gemini_rapido(api, PRINCIPAL, 0, timeout=0.2).complete(MENSAJES)
    assert time.perf_counter() - inicio < 1.0


def test_tope_de_reloj_respeta_el_plazo_del_turno() -> None:
    api = Api(_lento_sin_cortar(2.0))
    llm = FallbackLLMProvider(
        _gemini_rapido(api, PRINCIPAL, 1, timeout=20), _gemini_rapido(api, RESPALDO, 1, timeout=20)
    )
    inicio = time.perf_counter()
    with budget.turn_budget(0.3), pytest.raises(LLMBudgetExceededError):
        llm.complete(MENSAJES)
    assert time.perf_counter() - inicio < 1.0  # el plazo de 0,3 s manda sobre el timeout de 20 s
