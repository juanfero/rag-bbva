"""Estado de las dependencias del servicio, sin gastar tokens del LLM (M9).

- Qdrant: cuenta los puntos de la colección (una petición liviana).
- SQLite: lee la conversación más reciente.
- LLM: solo revisa la configuración (proveedor, modelo y si hay clave); no llama a la
  API, así `/health` no consume cupo ni créditos.
"""

import logging
from typing import Literal

from pydantic import BaseModel

from rag_bbva.config import Settings
from rag_bbva.exceptions import RagBbvaError
from rag_bbva.indexing.vector_store import VectorStore
from rag_bbva.memory.repository import ConversationRepository

logger = logging.getLogger(__name__)

ComponentStatus = Literal["ok", "down"]


class ComponentHealth(BaseModel):
    """Estado de una dependencia."""

    status: ComponentStatus
    detail: str | None = None


class QdrantHealth(ComponentHealth):
    collection: str
    points: int | None = None


class LLMHealth(ComponentHealth):
    provider: str
    model: str
    key_configured: bool


class HealthReport(BaseModel):
    """Estado general: `ok` si todo funciona; `degraded` si algo falla."""

    status: Literal["ok", "degraded"]
    qdrant: QdrantHealth
    sqlite: ComponentHealth
    llm: LLMHealth


def llm_key_configured(settings: Settings) -> bool:
    """¿Hay clave para el proveedor configurado? (el falso no necesita)."""
    if settings.llm_provider == "fake":
        return True
    clave = settings.gemini_api_key if settings.llm_provider == "gemini" else settings.xai_api_key
    return clave is not None and bool(clave.get_secret_value().strip())


class HealthChecker:
    """Revisa Qdrant, SQLite y la configuración del LLM."""

    def __init__(
        self, *, store: VectorStore, repository: ConversationRepository, settings: Settings
    ) -> None:
        self.store = store
        self.repository = repository
        self.settings = settings

    def _qdrant(self) -> QdrantHealth:
        coleccion = self.settings.qdrant_collection
        try:
            puntos = self.store.count()
        except RagBbvaError as exc:
            logger.warning("Qdrant no responde", extra={"error": str(exc)})
            return QdrantHealth(status="down", detail=exc.message, collection=coleccion)
        if puntos == 0:
            return QdrantHealth(
                status="down",
                detail="La colección está vacía: ejecute `python -m rag_bbva.cli ingest`",
                collection=coleccion,
                points=0,
            )
        return QdrantHealth(status="ok", collection=coleccion, points=puntos)

    def _sqlite(self) -> ComponentHealth:
        try:
            self.repository.list_conversations(limit=1)
        except RagBbvaError as exc:
            logger.warning("El historial no responde", extra={"error": str(exc)})
            return ComponentHealth(status="down", detail=exc.message)
        return ComponentHealth(status="ok")

    def _llm(self) -> LLMHealth:
        ajustes = self.settings
        hay_clave = llm_key_configured(ajustes)
        clave_env = "GEMINI_API_KEY" if ajustes.llm_provider == "gemini" else "XAI_API_KEY"
        return LLMHealth(
            status="ok" if hay_clave else "down",
            detail=None if hay_clave else f"Falta {clave_env} en .env",
            provider=ajustes.llm_provider,
            model=ajustes.llm_model,
            key_configured=hay_clave,
        )

    def check(self) -> HealthReport:
        """Estado de las tres dependencias."""
        qdrant, sqlite, llm = self._qdrant(), self._sqlite(), self._llm()
        todo_ok = all(c.status == "ok" for c in (qdrant, sqlite, llm))
        return HealthReport(
            status="ok" if todo_ok else "degraded", qdrant=qdrant, sqlite=sqlite, llm=llm
        )
