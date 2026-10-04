"""ApiClient de la interfaz (M10) contra una API simulada con respx: sin red."""

import json

import httpx
import pytest
import respx

from rag_bbva.exceptions import ApiClientError
from rag_bbva.ui.api_client import ApiClient

URL = "http://api-falsa:8000"

RESPUESTA_CHAT = {
    "conversation_id": "c-1",
    "message_id": 2,
    "question_message_id": 1,
    "answer": "Un CDT es un depósito [1].",
    "sources": [{"n": 1, "url": "https://www.bancolombia.com/glosario", "title": "Glosario"}],
    "no_answer": False,
    "gray_zone": False,
    "rewritten_query": None,
    "timings": {"rewrite": 0, "retrieval": 18, "rerank": 700, "llm": 1200, "total": 1950},
    "tokens": {"prompt": 1500, "completion": 80, "total": 1580},
    "model": "gemini-2.5-flash",
    "prompt_version": "2026-10-03.2",
}
SALUD = {
    "status": "degraded",
    "qdrant": {"status": "down", "detail": "No se pudo conectar con Qdrant", "collection": "c"},
    "sqlite": {"status": "ok", "detail": None},
    "llm": {
        "status": "ok",
        "detail": None,
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "fallback_model": "gemini-3.1-flash-lite",
        "key_configured": True,
    },
}


@pytest.fixture
def cliente() -> ApiClient:
    return ApiClient(URL, timeout=5)


@respx.mock
def test_chat_exito_envia_pregunta_e_id(cliente: ApiClient) -> None:
    ruta = respx.post(f"{URL}/chat").mock(return_value=httpx.Response(200, json=RESPUESTA_CHAT))

    r = cliente.chat("¿Qué es un CDT?", "c-1")

    assert r.answer == "Un CDT es un depósito [1]." and r.sources[0].n == 1
    assert r.timings.total == 1950 and r.tokens.total == 1580 and r.model == "gemini-2.5-flash"
    assert json.loads(ruta.calls[0].request.content) == {
        "question": "¿Qué es un CDT?",
        "conversation_id": "c-1",
    }


@respx.mock
def test_chat_sin_id_no_envia_conversation_id(cliente: ApiClient) -> None:
    ruta = respx.post(f"{URL}/chat").mock(return_value=httpx.Response(200, json=RESPUESTA_CHAT))
    cliente.chat("hola")
    assert json.loads(ruta.calls[0].request.content) == {"question": "hola"}


@pytest.mark.parametrize(
    ("codigo", "cuerpo"),
    [
        (404, {"error": "La conversación no existe", "detail": "c-x"}),
        (
            422,
            {"error": "Solicitud inválida", "detail": "question: La pregunta no puede estar vacía"},
        ),
        (
            503,
            {
                "error": "Se agotó el cupo diario de GEMINI_API_KEY (nivel gratuito).",
                "detail": None,
            },
        ),
    ],
)
@respx.mock
def test_errores_http_conservan_codigo_y_mensaje_de_la_api(
    cliente: ApiClient, codigo: int, cuerpo: dict[str, str | None]
) -> None:
    respx.post(f"{URL}/chat").mock(return_value=httpx.Response(codigo, json=cuerpo))
    with pytest.raises(ApiClientError) as error:
        cliente.chat("x", "c-x")
    assert error.value.status == codigo
    assert error.value.message == cuerpo["error"] and error.value.detail == cuerpo["detail"]


@respx.mock
def test_error_sin_json(cliente: ApiClient) -> None:
    respx.get(f"{URL}/conversations").mock(return_value=httpx.Response(502, text="Bad gateway"))
    with pytest.raises(ApiClientError, match="La API respondió 502") as error:
        cliente.list_conversations()
    assert error.value.status == 502


@respx.mock
def test_timeout_da_mensaje_amigable(cliente: ApiClient) -> None:
    respx.post(f"{URL}/chat").mock(side_effect=httpx.ReadTimeout("lento"))
    with pytest.raises(ApiClientError, match="tardando demasiado") as error:
        cliente.chat("x")
    assert error.value.status is None


@respx.mock
def test_api_caida_dice_como_levantarla(cliente: ApiClient) -> None:
    respx.get(f"{URL}/conversations").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ApiClientError, match="No se pudo conectar con la API") as error:
        cliente.list_conversations()
    assert "rag_bbva.cli serve" in error.value.message and "API_BASE_URL" in error.value.message


@respx.mock
def test_respuesta_inesperada(cliente: ApiClient) -> None:
    respx.post(f"{URL}/chat").mock(return_value=httpx.Response(200, json={"hola": 1}))
    with pytest.raises(ApiClientError, match="respuesta inesperada"):
        cliente.chat("x")


@respx.mock
def test_historial_feedback_y_lista(cliente: ApiClient) -> None:
    conversacion = {
        "id": "c-1",
        "title": "¿Qué es un CDT?",
        "created_at": "2026-10-03T15:00:00Z",
        "updated_at": "2026-10-03T15:01:00Z",
    }
    respx.get(f"{URL}/conversations", params={"limit": 20}).mock(
        return_value=httpx.Response(200, json=[conversacion])
    )
    respx.get(f"{URL}/conversations/c-1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "conversation": conversacion,
                "messages": [
                    {
                        "id": 1,
                        "role": "user",
                        "content": "¿Qué es un CDT?",
                        "created_at": "2026-10-03T15:00:00Z",
                        "sources": [],
                        "metrics": {},
                        "feedback": None,
                    },
                    {
                        "id": 2,
                        "role": "assistant",
                        "content": "Un CDT… [1]",
                        "created_at": "2026-10-03T15:00:01Z",
                        "sources": [{"n": 1, "url": "u", "title": "t"}],
                        "metrics": {"no_answer": False},
                        "feedback": "up",
                    },
                ],
            },
        )
    )
    voto = respx.post(f"{URL}/messages/2/feedback").mock(
        return_value=httpx.Response(200, json={"message_id": 2, "feedback": "down"})
    )

    assert [c.id for c in cliente.list_conversations(limit=20)] == ["c-1"]
    detalle = cliente.get_conversation("c-1")
    assert [m.role for m in detalle.messages] == ["user", "assistant"]
    assert detalle.messages[1].feedback == "up"
    cliente.send_feedback(2, "down")
    assert json.loads(voto.calls[0].request.content) == {"value": "down"}


@respx.mock
def test_health_503_se_devuelve_como_reporte_degradado(cliente: ApiClient) -> None:
    respx.get(f"{URL}/health").mock(return_value=httpx.Response(503, json=SALUD))
    salud = cliente.health()
    assert salud.status == "degraded" and salud.qdrant.status == "down"
    assert salud.llm.fallback_model == "gemini-3.1-flash-lite"


@respx.mock
def test_health_con_api_caida(cliente: ApiClient) -> None:
    respx.get(f"{URL}/health").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ApiClientError, match="No se pudo conectar"):
        cliente.health()
