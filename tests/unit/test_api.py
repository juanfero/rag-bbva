"""Pruebas de la API (M9) con TestClient y dobles: sin red, sin modelos, sin cupo."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rag_bbva.api.app import create_app, get_rag_service
from rag_bbva.config import Settings
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.memory.repository import InMemoryConversationRepository
from rag_bbva.memory.sql_repository import SqlAlchemyConversationRepository
from rag_bbva.services.rag_service import RAGService

from .fakes_rag import REESCRITA, LLMGuionado, RetrieverFalso, StoreFalso, ajustes, servicio


class Entorno:
    """App con dobles y acceso a ellos desde el test."""

    def __init__(
        self, settings: Settings, repo: InMemoryConversationRepository | None = None
    ) -> None:
        self.settings = settings
        self.repo = repo or InMemoryConversationRepository()
        self.llm = LLMGuionado()
        self.store = StoreFalso()
        self.retriever = RetrieverFalso(self.store)
        self.servicio = servicio(
            settings=settings, repo=self.repo, llm=self.llm, retriever=self.retriever
        )
        self.health = ComponentFactory(settings).create_health_checker(self.servicio)
        self.app = create_app(settings, service=self.servicio, health_checker=self.health)


@pytest.fixture
def entorno() -> Entorno:
    return Entorno(ajustes(chat_question_max_chars=60))


@pytest.fixture
def client(entorno: Entorno) -> Iterator[TestClient]:
    with TestClient(entorno.app) as cliente:  # `with` ejecuta el lifespan (warm_up)
        yield cliente


def _sin_trazas(cuerpo: dict[str, object]) -> None:
    texto = str(cuerpo)
    assert set(cuerpo) == {"error", "detail"}
    assert "Traceback" not in texto and 'File "' not in texto


# --- Flujo completo ------------------------------------------------------------------


def test_lifespan_calienta_los_modelos(entorno: Entorno, client: TestClient) -> None:
    assert entorno.retriever.calentado


def test_flujo_completo_de_chat_y_persistencia(entorno: Entorno, client: TestClient) -> None:
    r1 = client.post("/chat", json={"question": "  ¿Qué es el crédito de vivienda?  "})
    assert r1.status_code == 200, r1.text
    uno = r1.json()
    assert set(uno) >= {
        "conversation_id", "message_id", "answer", "sources", "no_answer",
        "rewritten_query", "timings", "tokens",
    }  # fmt: skip
    assert set(uno["timings"]) == {"rewrite", "retrieval", "rerank", "llm", "total"}
    assert set(uno["sources"][0]) == {"n", "url", "title"}
    assert uno["rewritten_query"] is None

    r2 = client.post(
        "/chat",
        json={
            "question": "¿y cuáles son los requisitos?",
            "conversation_id": uno["conversation_id"],
        },
    )
    dos = r2.json()
    assert r2.status_code == 200
    assert dos["conversation_id"] == uno["conversation_id"]
    assert dos["rewritten_query"] == REESCRITA
    # Prueba de memoria: el FakeLLM recibió el historial para reformular.
    prompt = entorno.llm.llamadas_de_reformulacion()[0][1]["content"]
    assert "Usuario: ¿Qué es el crédito de vivienda?" in prompt

    conversaciones = client.get("/conversations").json()
    assert [c["id"] for c in conversaciones] == [uno["conversation_id"]]
    assert conversaciones[0]["title"] == "¿Qué es el crédito de vivienda?"

    mensajes = client.get(f"/conversations/{uno['conversation_id']}/messages").json()
    assert [m["role"] for m in mensajes["messages"]] == ["user", "assistant"] * 2
    assert mensajes["messages"][0]["content"] == "¿Qué es el crédito de vivienda?"
    assert mensajes["messages"][1]["id"] == uno["message_id"]
    assert mensajes["messages"][1]["metrics"]["top_score"] == 4.0
    assert mensajes["messages"][3]["sources"] == dos["sources"]


def test_feedback(entorno: Entorno, client: TestClient) -> None:
    turno = client.post("/chat", json={"question": "¿Qué es un CDT?"}).json()

    ok = client.post(f"/messages/{turno['message_id']}/feedback", json={"value": "down"})
    assert ok.status_code == 200
    assert ok.json() == {"message_id": turno["message_id"], "feedback": "down"}
    mensajes = client.get(f"/conversations/{turno['conversation_id']}/messages").json()
    assert mensajes["messages"][1]["feedback"] == "down"

    pregunta = client.post(
        f"/messages/{turno['question_message_id']}/feedback", json={"value": "up"}
    )
    assert pregunta.status_code == 404
    assert client.post("/messages/9999/feedback", json={"value": "up"}).status_code == 404
    invalido = client.post(f"/messages/{turno['message_id']}/feedback", json={"value": "meh"})
    assert invalido.status_code == 422
    _sin_trazas(invalido.json())


# --- 404 ------------------------------------------------------------------------------


def test_conversacion_inexistente_404_sin_gastar_tokens(
    entorno: Entorno, client: TestClient
) -> None:
    r = client.post(
        "/chat", json={"question": "¿y los requisitos?", "conversation_id": "no-existe"}
    )

    assert r.status_code == 404
    assert r.json() == {"error": "La conversación no existe", "detail": "no-existe"}
    assert entorno.llm.calls == []
    assert client.get("/conversations/no-existe/messages").status_code == 404
    assert client.get("/conversations").json() == []


def test_ruta_inexistente_y_sin_analytics(client: TestClient) -> None:
    r = client.get("/analytics/summary")  # llega en M11
    assert r.status_code == 404
    assert r.json() == {"error": "Recurso no encontrado", "detail": None}


# --- 422 ------------------------------------------------------------------------------


@pytest.mark.parametrize("pregunta", ["", "   ", "\n\t "])
def test_pregunta_vacia_422(entorno: Entorno, client: TestClient, pregunta: str) -> None:
    r = client.post("/chat", json={"question": pregunta})
    assert r.status_code == 422
    assert r.json() == {
        "error": "Solicitud inválida",
        "detail": "question: La pregunta no puede estar vacía",
    }
    assert entorno.llm.calls == []


def test_pregunta_demasiado_larga_422(entorno: Entorno, client: TestClient) -> None:
    r = client.post("/chat", json={"question": "x" * 61})
    assert r.status_code == 422
    assert r.json()["detail"] == "question: La pregunta supera el máximo de 60 caracteres"
    assert client.post("/chat", json={"question": "x" * 60}).status_code == 200


def test_cuerpo_invalido_422(client: TestClient) -> None:
    for cuerpo in ({}, {"question": 5}, {"question": "hola", "conversation_id": "x" * 65}):
        r = client.post("/chat", json=cuerpo)
        assert r.status_code == 422, cuerpo
        _sin_trazas(r.json())
    assert client.get("/conversations?limit=0").status_code == 422


# --- 503 y 500 ------------------------------------------------------------------------


def test_llm_falla_503_y_turno_atomico(entorno: Entorno, client: TestClient) -> None:
    cid = client.post("/chat", json={"question": "¿Qué es un CDT?"}).json()["conversation_id"]
    entorno.llm.falla_respuesta = True

    r = client.post("/chat", json={"question": "¿y su plazo?", "conversation_id": cid})

    assert r.status_code == 503
    assert r.json() == {"error": "Se agotó el cupo diario del LLM. Intente mañana.", "detail": None}
    assert "429" not in r.text  # el detalle técnico del proveedor no llega al cliente
    assert len(client.get(f"/conversations/{cid}/messages").json()["messages"]) == 2


def test_qdrant_caido_503(entorno: Entorno, client: TestClient) -> None:
    entorno.store.caido = True
    r = client.post("/chat", json={"question": "¿Qué es un CDT?"})
    assert r.status_code == 503
    assert r.json()["error"].startswith("El servicio de búsqueda no está disponible")
    assert "qdrant-falso" not in r.text
    assert client.get("/conversations").json() == []


def test_llm_sin_clave_503(tmp_path: Path) -> None:
    """Sin GEMINI_API_KEY la API arranca, /health lo informa y /chat responde 503."""
    config = ajustes(llm_provider="gemini", gemini_api_key=None)
    rag = ComponentFactory(config).create_rag_service(
        repository=InMemoryConversationRepository(),
        retriever=RetrieverFalso(),
        tolerate_missing_llm=True,
    )
    app = create_app(
        config, service=rag, health_checker=ComponentFactory(config).create_health_checker(rag)
    )
    with TestClient(app) as cliente:
        r = cliente.post("/chat", json={"question": "¿Qué es un CDT?"})
        salud = cliente.get("/health")

    assert r.status_code == 503
    assert r.json()["error"] == "El asistente no está configurado por completo"
    assert "GEMINI_API_KEY" in r.json()["detail"]
    assert salud.status_code == 503
    assert salud.json()["llm"] == {
        "status": "down", "detail": "Falta GEMINI_API_KEY en .env",
        "provider": "gemini", "model": "gemini-2.5-flash",
        "fallback_model": "gemini-3.1-flash-lite", "key_configured": False,
    }  # fmt: skip


def test_error_inesperado_500_sin_trazas(entorno: Entorno) -> None:
    class Roto(RAGService):
        def __init__(self) -> None:
            pass

        def list_conversations(self, limit: int = 50) -> list:  # type: ignore[override,type-arg]
            raise RuntimeError("detalle interno secreto")

    entorno.app.dependency_overrides[get_rag_service] = Roto
    with TestClient(entorno.app, raise_server_exceptions=False) as cliente:
        r = cliente.get("/conversations")

    assert r.status_code == 500
    assert r.json() == {
        "error": "Ocurrió un error interno. Intente de nuevo más tarde.",
        "detail": None,
    }
    assert "secreto" not in r.text


# --- /health --------------------------------------------------------------------------


def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "qdrant": {
            "status": "ok", "detail": None, "collection": "bancolombia_docs", "points": 3506,
        },
        "sqlite": {"status": "ok", "detail": None},
        "llm": {
            "status": "ok", "detail": None, "provider": "fake", "model": "gemini-2.5-flash",
            "fallback_model": "gemini-3.1-flash-lite", "key_configured": True,
        },
    }  # fmt: skip


def test_health_con_qdrant_caido_y_recuperado(entorno: Entorno, client: TestClient) -> None:
    entorno.store.caido = True
    caido = client.get("/health")
    assert caido.status_code == 503
    assert caido.json()["status"] == "degraded"
    assert caido.json()["qdrant"]["status"] == "down"
    assert caido.json()["sqlite"]["status"] == "ok"

    entorno.store.caido = False
    assert client.get("/health").status_code == 200


def test_health_con_coleccion_vacia(entorno: Entorno, client: TestClient) -> None:
    entorno.store.puntos = 0
    r = client.get("/health")
    assert r.status_code == 503
    assert "ingest" in r.json()["qdrant"]["detail"]


def test_health_con_sqlite_caido(tmp_path: Path) -> None:
    ruta = tmp_path / "history.db"
    repo = SqlAlchemyConversationRepository.from_path(ruta)
    entorno = Entorno(ajustes(), repo=repo)  # type: ignore[arg-type]
    repo.close()
    ruta.unlink()
    ruta.mkdir()  # el archivo de la base pasa a ser una carpeta: SQLite no puede abrirlo
    for extra in ("history.db-wal", "history.db-shm"):
        (tmp_path / extra).unlink(missing_ok=True)

    with TestClient(entorno.app) as cliente:
        r = cliente.get("/health")
        chat = cliente.post("/chat", json={"question": "¿Qué es un CDT?"})

    assert r.status_code == 503
    assert r.json()["sqlite"]["status"] == "down"
    assert chat.status_code == 503
    assert chat.json() == {
        "error": "El historial de conversaciones no está disponible en este momento.",
        "detail": None,
    }


# --- Inyección de dependencias y fábrica ------------------------------------------------


def test_dependency_overrides_sustituye_el_servicio(entorno: Entorno) -> None:
    otro = Entorno(ajustes())
    otro.servicio.ask(None, "conversación del servicio sustituto")
    entorno.app.dependency_overrides[get_rag_service] = lambda: otro.servicio
    with TestClient(entorno.app) as cliente:
        titulos = [c["title"] for c in cliente.get("/conversations").json()]
    assert titulos == ["conversación del servicio sustituto"]


def test_lifespan_construye_los_componentes_desde_la_configuracion(tmp_path: Path) -> None:
    """Sin componentes inyectados, el lifespan los crea con la fábrica (aquí, falsos)."""
    config = ajustes(
        reranker_enabled=False,
        qdrant_url=":memory:",
        history_db_path=tmp_path / "h" / "history.db",
    )
    app = create_app(config)
    with TestClient(app) as cliente:
        salud = cliente.get("/health").json()
        docs = cliente.get("/docs")
        openapi = cliente.get("/openapi.json").json()

    assert salud["sqlite"]["status"] == "ok"
    assert salud["qdrant"]["status"] == "down"  # la colección de prueba no existe
    assert (tmp_path / "h" / "history.db").is_file()
    assert docs.status_code == 200
    assert set(openapi["paths"]) == {
        "/chat", "/conversations", "/conversations/{conversation_id}/messages",
        "/messages/{message_id}/feedback", "/health",
    }  # fmt: skip
    assert openapi["info"]["title"] == "Asistente Bancolombia (RAG)"
