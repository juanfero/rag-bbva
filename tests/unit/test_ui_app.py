"""Flujos de la interfaz Streamlit (M10) con AppTest y un ApiClient falso: sin red."""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from rag_bbva.exceptions import ApiClientError
from rag_bbva.ui.api_client import ChatReply, ConversationDetail, ConversationSummary, HealthStatus
from rag_bbva.ui.render import AVISO, SIN_INFO_TITULO

APP = str(Path(__file__).resolve().parents[2] / "src" / "rag_bbva" / "ui" / "app.py")
B = "https://www.bancolombia.com"


def _respuesta(n: int, conversation_id: str = "c-1", **cambios: Any) -> ChatReply:
    base = {
        "conversation_id": conversation_id,
        "message_id": 2 * n,
        "question_message_id": 2 * n - 1,
        "answer": f"Respuesta {n}: el CDT es un depósito [1].",
        "sources": [{"n": 1, "url": f"{B}/glosario", "title": "Glosario"}],
        "no_answer": False,
        "gray_zone": False,
        "rewritten_query": None,
        "timings": {"rewrite": 0, "retrieval": 18, "rerank": 700, "llm": 1200, "total": 1950},
        "tokens": {"prompt": 1500, "completion": 80, "total": 1580},
        "model": "gemini-2.5-flash",
    }
    return ChatReply.model_validate({**base, **cambios})


class ApiFalsa:
    """Doble de ApiClient: respuestas guionadas y registro de llamadas."""

    def __init__(self) -> None:
        self.respuestas: list[ChatReply | ApiClientError] = []
        self.preguntas: list[tuple[str, str | None]] = []
        self.votos: list[tuple[int, str]] = []
        self.conversaciones: list[ConversationSummary] = []
        self.historiales: dict[str, ConversationDetail] = {}
        self.salud: HealthStatus | ApiClientError = HealthStatus.model_validate(
            {
                "status": "ok",
                "qdrant": {"status": "ok"},
                "sqlite": {"status": "ok"},
                "llm": {
                    "status": "ok",
                    "model": "gemini-2.5-flash",
                    "fallback_model": "gemini-3.1-flash-lite",
                },
            }
        )

    def chat(self, question: str, conversation_id: str | None = None) -> ChatReply:
        self.preguntas.append((question, conversation_id))
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, ApiClientError):
            raise siguiente
        return siguiente

    def list_conversations(self, limit: int = 20) -> list[ConversationSummary]:
        return self.conversaciones

    def get_conversation(self, conversation_id: str) -> ConversationDetail:
        if conversation_id not in self.historiales:
            raise ApiClientError("La conversación no existe", status=404, detail=conversation_id)
        return self.historiales[conversation_id]

    def send_feedback(self, message_id: int, value: str) -> None:
        self.votos.append((message_id, value))

    def health(self) -> HealthStatus:
        if isinstance(self.salud, ApiClientError):
            raise self.salud
        return self.salud


@pytest.fixture
def api() -> ApiFalsa:
    return ApiFalsa()


def _app(api: ApiFalsa) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["api_client"] = api
    return at.run()


def _textos(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def _preguntar(at: AppTest, pregunta: str) -> AppTest:
    return at.chat_input[0].set_value(pregunta).run()


def test_pantalla_inicial(api: ApiFalsa) -> None:
    at = _app(api)
    assert not at.exception
    assert at.title[0].value == "Asistente de información pública"
    assert at.info[0].value == AVISO
    assert "(nueva: se crea con la primera pregunta)" in at.sidebar.code[0].value
    estado = "\n".join(m.value for m in at.sidebar.markdown)
    assert "🟢 **Búsqueda (Qdrant)**" in estado and "🟢 **LLM**" in estado
    assert "gemini-2.5-flash (respaldo: gemini-3.1-flash-lite)" in estado
    assert len(at.chat_message) == 0


def test_conversacion_con_citas_fuentes_y_memoria_del_id(api: ApiFalsa) -> None:
    api.respuestas = [_respuesta(1), _respuesta(2)]
    at = _app(api)

    at = _preguntar(at, "¿Qué es un CDT?")
    assert not at.exception
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert f"[\\[1\\]]({B}/glosario)" in _textos(at)  # la cita es un enlace
    assert [e.label for e in at.expander] == ["Fuentes (1)"]
    assert at.session_state["conversation_id"] == "c-1"
    assert "c-1" in at.sidebar.code[0].value

    at = _preguntar(at, "¿y su plazo?")
    assert api.preguntas == [("¿Qué es un CDT?", None), ("¿y su plazo?", "c-1")]
    assert len(at.chat_message) == 4


def test_no_answer_tiene_estilo_propio(api: ApiFalsa) -> None:
    api.respuestas = [
        _respuesta(1, answer="No encontré información suficiente…", sources=[], no_answer=True)
    ]
    at = _preguntar(_app(api), "receta de arepas")
    assert [w.value for w in at.warning] == [f"**{SIN_INFO_TITULO}**"]
    assert len(at.expander) == 0  # sin fuentes


def test_feedback_envia_y_deshabilita_los_botones(api: ApiFalsa) -> None:
    api.respuestas = [_respuesta(1)]
    at = _preguntar(_app(api), "¿Qué es un CDT?")
    assert not at.button(key="up_2").disabled

    at = at.button(key="up_2").click().run()

    assert api.votos == [(2, "up")]
    assert at.button(key="up_2").disabled and at.button(key="down_2").disabled


@pytest.mark.parametrize(
    ("error", "texto"),
    [
        (
            ApiClientError("Se agotó el cupo diario.", status=503),
            "El asistente no está disponible en este momento. Se agotó el cupo diario.",
        ),
        (
            ApiClientError(
                "Solicitud inválida",
                status=422,
                detail="question: La pregunta supera el máximo de 1000 caracteres",
            ),
            "La pregunta no es válida: La pregunta supera el máximo de 1000 caracteres",
        ),
        (
            ApiClientError("La respuesta está tardando demasiado."),
            "La respuesta está tardando demasiado.",
        ),
    ],
)
def test_errores_amigables_sin_perder_la_pregunta(
    api: ApiFalsa, error: ApiClientError, texto: str
) -> None:
    api.respuestas = [error]
    at = _preguntar(_app(api), "¿Qué es un CDT?")
    assert not at.exception
    assert at.error[0].value == f"No se pudo responder «¿Qué es un CDT?». {texto}"
    assert at.session_state["messages"] == []  # el turno no se guardó (ADR-014)


def test_conversacion_inexistente_404_vuelve_a_una_nueva(api: ApiFalsa) -> None:
    api.respuestas = [ApiClientError("La conversación no existe", status=404)]
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["api_client"] = api
    at.session_state["conversation_id"] = "borrada"
    at = _preguntar(at.run(), "¿y su plazo?")
    assert "No encontré esa conversación" in at.error[0].value
    assert at.session_state["conversation_id"] is None


def _historial() -> ConversationDetail:
    return ConversationDetail.model_validate(
        {
            "conversation": {
                "id": "c-9",
                "title": "Crédito de vivienda",
                "created_at": "2026-10-03T15:00:00Z",
                "updated_at": "2026-10-03T15:05:00Z",
            },
            "messages": [
                {
                    "id": 17,
                    "role": "user",
                    "content": "¿Qué es el crédito de vivienda?",
                    "created_at": "2026-10-03T15:00:00Z",
                    "sources": [],
                    "metrics": {},
                    "feedback": None,
                },
                {
                    "id": 18,
                    "role": "assistant",
                    "content": "Financia tu casa [1].",
                    "created_at": "2026-10-03T15:00:03Z",
                    "sources": [{"n": 1, "url": f"{B}/vivienda", "title": "Vivienda"}],
                    "metrics": {
                        "no_answer": False,
                        "total_ms": 2300.0,
                        "top_score": 6.5,
                        "prompt_tokens": 1500,
                        "completion_tokens": 120,
                    },
                    "feedback": "down",
                },
            ],
        }
    )


def test_retomar_desde_la_lista(api: ApiFalsa) -> None:
    api.historiales["c-9"] = _historial()
    api.conversaciones = [_historial().conversation]
    at = _app(api)

    at = at.button(key="conv_c-9").click().run()

    assert at.session_state["conversation_id"] == "c-9"
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert f"[\\[1\\]]({B}/vivienda)" in _textos(at)
    assert at.button(key="down_18").disabled  # ya estaba valorada


def test_retomar_por_id_y_por_id_inexistente(api: ApiFalsa) -> None:
    api.historiales["c-9"] = _historial()
    at = _app(api)

    at.text_input[0].input("no-existe")
    at = at.button[1].click().run()  # "Retomar por ID"
    assert "No encontré esa conversación" in at.error[0].value
    assert at.session_state["conversation_id"] is None

    at.text_input[0].input("  c-9 ")
    at = at.button[1].click().run()
    assert at.session_state["conversation_id"] == "c-9"
    assert len(at.chat_message) == 2


def test_nueva_conversacion_limpia_el_chat(api: ApiFalsa) -> None:
    api.respuestas = [_respuesta(1)]
    at = _preguntar(_app(api), "¿Qué es un CDT?")
    at = at.sidebar.button[0].click().run()  # "Nueva conversación"
    assert at.session_state["conversation_id"] is None
    assert len(at.chat_message) == 0


def test_modo_detalle(api: ApiFalsa) -> None:
    api.respuestas = [
        _respuesta(
            1,
            rewritten_query="¿Qué es un CDT de Bancolombia?",
            gray_zone=True,
            model="gemini-3.1-flash-lite",
        )
    ]
    at = _preguntar(_app(api), "¿y un CDT?")
    assert "Detalle técnico" not in [e.label for e in at.expander]

    at = at.toggle[0].set_value(True).run()

    assert "Detalle técnico" in [e.label for e in at.expander]
    texto = _textos(at)
    assert "**Pregunta reformulada:** ¿Qué es un CDT de Bancolombia?" in texto
    assert "**Modelo:** gemini-3.1-flash-lite" in texto
    assert (
        "**Tiempos (ms):** rewrite 0 · retrieval 18 · rerank 700 · llm 1,200 · total 1,950" in texto
    )


def test_estado_degradado_y_api_caida(api: ApiFalsa) -> None:
    api.salud = HealthStatus.model_validate(
        {
            "status": "degraded",
            "qdrant": {"status": "down", "detail": "No se pudo conectar con Qdrant"},
            "sqlite": {"status": "ok"},
            "llm": {"status": "ok", "model": "m"},
        }
    )
    estado = "\n".join(m.value for m in _app(api).sidebar.markdown)
    assert "🔴 **Búsqueda (Qdrant)**" in estado and "No se pudo conectar con Qdrant" in estado

    api.salud = ApiClientError("No se pudo conectar con la API en http://127.0.0.1:8000.")
    estado = "\n".join(m.value for m in _app(api).sidebar.markdown)
    assert "🔴 **API**" in estado
