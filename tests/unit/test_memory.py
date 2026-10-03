"""Pruebas del historial de conversaciones (M8).

Las pruebas de contrato corren contra las tres variantes del repositorio: en memoria,
SQLite en memoria y SQLite en archivo. Las de persistencia "reinician" el repositorio
creando una instancia nueva sobre el mismo archivo.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rag_bbva.exceptions import ConversationNotFoundError, HistoryError, MessageNotFoundError
from rag_bbva.memory import (
    ConversationRepository,
    InMemoryConversationRepository,
    MessageMetrics,
    SqlAlchemyConversationRepository,
)
from rag_bbva.memory.models import TITLE_MAX_CHARS, title_from_question

INICIO = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


class RelojFalso:
    """Reloj que avanza un segundo en cada lectura."""

    def __init__(self) -> None:
        self.ahora = INICIO

    def __call__(self) -> datetime:
        valor = self.ahora
        self.ahora += timedelta(seconds=1)
        return valor


def _sql_archivo(tmp_path: Path, reloj: Callable[[], datetime]) -> ConversationRepository:
    return SqlAlchemyConversationRepository.from_path(tmp_path / "h" / "history.db", reloj)


FABRICAS: dict[str, Callable[[Path, Callable[[], datetime]], ConversationRepository]] = {
    "memoria": lambda _tmp, reloj: InMemoryConversationRepository(reloj),
    "sqlite_memoria": lambda _tmp, reloj: SqlAlchemyConversationRepository.in_memory(reloj),
    "sqlite_archivo": _sql_archivo,
}


@pytest.fixture(params=sorted(FABRICAS))
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ConversationRepository]:
    repositorio = FABRICAS[request.param](tmp_path, RelojFalso())
    yield repositorio
    if isinstance(repositorio, SqlAlchemyConversationRepository):
        repositorio.close()


def _turnos(repo: ConversationRepository, conversation_id: str, cantidad: int) -> None:
    """Agrega `cantidad` pares pregunta/respuesta numerados."""
    for i in range(1, cantidad + 1):
        repo.add_message(conversation_id, "user", f"pregunta {i}")
        repo.add_message(conversation_id, "assistant", f"respuesta {i}")


# --- Contrato común ---------------------------------------------------------------


def test_crea_conversacion_vacia(repo: ConversationRepository) -> None:
    conversacion = repo.create_conversation()
    assert conversacion.title is None
    assert conversacion.created_at == conversacion.updated_at == INICIO
    assert repo.get_conversation(conversacion.id) == conversacion
    assert repo.get_messages(conversacion.id) == []


def test_ids_de_conversacion_son_unicos(repo: ConversationRepository) -> None:
    ids = {repo.create_conversation().id for _ in range(5)}
    assert len(ids) == 5


def test_get_last_n_respeta_n_y_orden_cronologico(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    _turnos(repo, cid, 5)

    ultimos = repo.get_last_n(cid, 3)

    assert [m.content for m in ultimos] == ["respuesta 4", "pregunta 5", "respuesta 5"]
    assert [m.created_at for m in ultimos] == sorted(m.created_at for m in ultimos)


def test_get_last_n_cero_devuelve_vacio(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    _turnos(repo, cid, 2)
    assert repo.get_last_n(cid, 0) == []


def test_get_last_n_mayor_que_el_total_devuelve_todo(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    _turnos(repo, cid, 2)
    assert [m.content for m in repo.get_last_n(cid, 50)] == [
        "pregunta 1",
        "respuesta 1",
        "pregunta 2",
        "respuesta 2",
    ]


def test_get_last_n_negativo_falla(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    with pytest.raises(HistoryError, match="negativa"):
        repo.get_last_n(cid, -1)


def test_orden_por_insercion_aunque_coincida_la_hora(tmp_path: Path) -> None:
    """Con reloj fijo todos los mensajes comparten hora: el orden lo da la inserción."""
    for fabrica in FABRICAS.values():
        repo = fabrica(tmp_path / str(id(fabrica)), lambda: INICIO)
        cid = repo.create_conversation().id
        _turnos(repo, cid, 3)
        assert [m.content for m in repo.get_last_n(cid, 2)] == ["pregunta 3", "respuesta 3"]


def test_conversaciones_distintas_no_se_mezclan(repo: ConversationRepository) -> None:
    a = repo.create_conversation().id
    b = repo.create_conversation().id
    repo.add_message(a, "user", "¿Qué es un CDT?")
    repo.add_message(b, "user", "¿Cómo pido un crédito de vivienda?")
    repo.add_message(a, "assistant", "Un CDT es…")

    assert [m.content for m in repo.get_messages(a)] == ["¿Qué es un CDT?", "Un CDT es…"]
    assert [m.content for m in repo.get_last_n(b, 6)] == ["¿Cómo pido un crédito de vivienda?"]
    assert {m.conversation_id for m in repo.get_messages(a)} == {a}


def test_conversacion_inexistente_se_informa_y_no_se_crea(repo: ConversationRepository) -> None:
    """Contrato: un ID desconocido lanza `ConversationNotFoundError` en lectura y escritura."""
    assert repo.get_conversation("no-existe") is None
    with pytest.raises(ConversationNotFoundError):
        repo.get_last_n("no-existe", 6)
    with pytest.raises(ConversationNotFoundError):
        repo.get_messages("no-existe")
    with pytest.raises(ConversationNotFoundError):
        repo.add_message("no-existe", "user", "hola")
    with pytest.raises(ConversationNotFoundError):
        repo.require_conversation("no-existe")
    assert repo.get_conversation("no-existe") is None
    assert repo.list_conversations() == []


def test_conversation_not_found_es_un_history_error() -> None:
    assert issubclass(ConversationNotFoundError, HistoryError)
    assert issubclass(MessageNotFoundError, HistoryError)


def test_titulo_es_la_primera_pregunta(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    repo.add_message(cid, "user", "  ¿Qué   es\nun CDT?  ")
    repo.add_message(cid, "user", "¿Y su tasa?")
    assert repo.require_conversation(cid).title == "¿Qué es un CDT?"


def test_titulo_explicito_no_se_reemplaza(repo: ConversationRepository) -> None:
    cid = repo.create_conversation(title="Vivienda").id
    repo.add_message(cid, "user", "¿Qué es leasing habitacional?")
    assert repo.require_conversation(cid).title == "Vivienda"


def test_titulo_largo_se_recorta() -> None:
    titulo = title_from_question("palabra " * 40)
    assert len(titulo) == TITLE_MAX_CHARS
    assert titulo.endswith("…")


def test_add_message_actualiza_updated_at(repo: ConversationRepository) -> None:
    conversacion = repo.create_conversation()
    mensaje = repo.add_message(conversacion.id, "user", "hola")
    actualizada = repo.require_conversation(conversacion.id)
    assert actualizada.updated_at == mensaje.created_at > conversacion.created_at
    assert actualizada.created_at == conversacion.created_at


def test_list_conversations_mas_recientes_primero(repo: ConversationRepository) -> None:
    vieja = repo.create_conversation().id
    nueva = repo.create_conversation().id
    repo.add_message(vieja, "user", "vuelvo a esta")
    assert [c.id for c in repo.list_conversations()] == [vieja, nueva]
    assert [c.id for c in repo.list_conversations(limit=1)] == [vieja]


def test_guarda_fuentes_y_metricas(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    fuentes = [{"number": 1, "url": "https://www.bancolombia.com/personas", "title": "Ñandú"}]
    metricas = MessageMetrics(
        retrieval_ms=12.5,
        rerank_ms=80.0,
        llm_ms=950.2,
        total_ms=1050.1,
        top_score=3.2,
        no_answer=False,
        prompt_tokens=1200,
        completion_tokens=150,
    )
    guardado = repo.add_message(
        cid, "assistant", "Respuesta [1]", sources=fuentes, metrics=metricas
    )

    (leido,) = repo.get_messages(cid)
    assert leido == guardado
    assert leido.sources == fuentes
    assert leido.metrics == metricas
    assert leido.created_at.tzinfo is not None


def test_mensaje_sin_metricas_las_deja_vacias(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    mensaje = repo.add_message(cid, "user", "hola")
    assert mensaje.metrics == MessageMetrics()
    assert mensaje.sources == []
    assert mensaje.feedback is None


def test_as_chat_message(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    _turnos(repo, cid, 1)
    assert [m.as_chat_message() for m in repo.get_last_n(cid, 6)] == [
        {"role": "user", "content": "pregunta 1"},
        {"role": "assistant", "content": "respuesta 1"},
    ]


@pytest.mark.parametrize(("rol", "contenido"), [("system", "hola"), ("user", "   ")])
def test_rechaza_rol_invalido_y_contenido_vacio(
    repo: ConversationRepository, rol: str, contenido: str
) -> None:
    cid = repo.create_conversation().id
    with pytest.raises(HistoryError):
        repo.add_message(cid, rol, contenido)  # type: ignore[arg-type]
    assert repo.get_messages(cid) == []


def test_feedback_se_guarda_y_se_quita(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    _turnos(repo, cid, 1)
    respuesta = repo.get_messages(cid)[1]

    assert repo.set_feedback(respuesta.id, "up").feedback == "up"
    assert repo.get_messages(cid)[1].feedback == "up"
    assert repo.set_feedback(respuesta.id, "down").feedback == "down"
    assert repo.set_feedback(respuesta.id, None).feedback is None
    assert repo.get_messages(cid)[1].feedback is None


def test_feedback_invalido_o_mensaje_inexistente(repo: ConversationRepository) -> None:
    cid = repo.create_conversation().id
    mensaje = repo.add_message(cid, "assistant", "respuesta")
    with pytest.raises(HistoryError, match="Valoración"):
        repo.set_feedback(mensaje.id, "meh")  # type: ignore[arg-type]
    with pytest.raises(MessageNotFoundError):
        repo.set_feedback(9999, "up")


# --- Persistencia en SQLite ----------------------------------------------------------


def test_persiste_tras_reiniciar(tmp_path: Path) -> None:
    ruta = tmp_path / "history" / "history.db"
    primero = SqlAlchemyConversationRepository.from_path(ruta, RelojFalso())
    cid = primero.create_conversation().id
    _turnos(primero, cid, 4)
    respuesta = primero.add_message(
        cid, "assistant", "con fuente", sources=[{"url": "u"}], metrics=MessageMetrics(llm_ms=5)
    )
    primero.set_feedback(respuesta.id, "up")
    antes = primero.get_messages(cid)
    primero.close()

    reiniciado = SqlAlchemyConversationRepository.from_path(ruta)

    assert ruta.is_file()
    assert reiniciado.get_messages(cid) == antes
    assert [m.content for m in reiniciado.get_last_n(cid, 2)] == ["respuesta 4", "con fuente"]
    assert reiniciado.require_conversation(cid).title == "pregunta 1"
    assert reiniciado.get_messages(cid)[-1].feedback == "up"
    reiniciado.close()


def test_ids_de_mensaje_siguen_creciendo_tras_reiniciar(tmp_path: Path) -> None:
    ruta = tmp_path / "history.db"
    primero = SqlAlchemyConversationRepository.from_path(ruta)
    cid = primero.create_conversation().id
    ultimo = primero.add_message(cid, "user", "uno")
    primero.close()

    segundo = SqlAlchemyConversationRepository.from_path(ruta)
    nuevo = segundo.add_message(cid, "assistant", "dos")
    assert nuevo.id > ultimo.id
    assert [m.content for m in segundo.get_last_n(cid, 6)] == ["uno", "dos"]
    segundo.close()


def test_crea_las_tablas_del_plan(tmp_path: Path) -> None:
    import sqlite3

    ruta = tmp_path / "history.db"
    SqlAlchemyConversationRepository.from_path(ruta).close()
    with sqlite3.connect(ruta) as conexion:
        columnas = {
            tabla: [fila[1] for fila in conexion.execute(f"PRAGMA table_info({tabla})")]
            for tabla in ("conversations", "messages")
        }
    assert columnas["conversations"] == ["id", "created_at", "updated_at", "title"]
    assert columnas["messages"] == [
        "id",
        "conversation_id",
        "role",
        "content",
        "created_at",
        "sources_json",
        "retrieval_ms",
        "rerank_ms",
        "llm_ms",
        "total_ms",
        "top_score",
        "no_answer",
        "prompt_tokens",
        "completion_tokens",
        "feedback",
    ]


def test_ruta_invalida_lanza_history_error(tmp_path: Path) -> None:
    archivo = tmp_path / "soy_un_archivo"
    archivo.write_text("x")
    with pytest.raises(HistoryError, match="carpeta"):
        SqlAlchemyConversationRepository.from_path(archivo / "history.db")


def test_archivo_que_no_es_sqlite_lanza_history_error(tmp_path: Path) -> None:
    ruta = tmp_path / "history.db"
    ruta.write_bytes(b"esto no es una base SQLite " * 100)
    with pytest.raises(HistoryError, match="esquema"):
        SqlAlchemyConversationRepository.from_path(ruta)


def test_fabrica_crea_repositorio_sqlite_en_history_db_path(tmp_path: Path) -> None:
    from rag_bbva.config import Settings
    from rag_bbva.indexing.factory import ComponentFactory

    ruta = tmp_path / "datos" / "history.db"
    repo = ComponentFactory(Settings(history_db_path=ruta)).create_conversation_repository()

    assert isinstance(repo, SqlAlchemyConversationRepository)
    repo.create_conversation()
    assert ruta.is_file()
    repo.close()
