"""Dobles compartidos por las pruebas del servicio RAG y de la API (M9). Sin red."""

from collections.abc import Sequence

from rag_bbva.config import Settings
from rag_bbva.exceptions import IndexingError, LLMError
from rag_bbva.indexing.embedding import FakeEmbedder
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.indexing.vector_store import SearchHit, VectorPoint, VectorStore
from rag_bbva.llm.prompts import REWRITE_SYSTEM_PROMPT
from rag_bbva.llm.provider import FakeLLMProvider, Message
from rag_bbva.memory.repository import ConversationRepository, InMemoryConversationRepository
from rag_bbva.retrieval.models import Candidate, RetrievalResult
from rag_bbva.retrieval.reranker import NoOpReranker
from rag_bbva.retrieval.retriever import Retriever
from rag_bbva.services.rag_service import RAGService

B = "https://www.bancolombia.com"
REESCRITA = "¿Cuáles son los requisitos del crédito de vivienda de Bancolombia?"


class StoreFalso(VectorStore):
    """Almacén vectorial que solo cuenta puntos; `caido=True` simula Qdrant detenido."""

    def __init__(self, puntos: int = 3506) -> None:
        self.puntos = puntos
        self.caido = False

    def _revisar(self) -> None:
        if self.caido:
            raise IndexingError("No se pudo conectar con Qdrant en http://qdrant-falso:6333")

    def count(self) -> int:
        self._revisar()
        return self.puntos

    def ensure_collection(self, dimension: int, *, recreate: bool = False) -> None:
        raise NotImplementedError

    def upsert(self, points: Sequence[VectorPoint]) -> None:
        raise NotImplementedError

    def search(
        self, vector: Sequence[float], limit: int, *, section: str | None = None
    ) -> list[SearchHit]:
        raise NotImplementedError

    def delete(self, ids: Sequence[str]) -> None:
        raise NotImplementedError

    def stored_hashes(self) -> dict[str, str]:
        raise NotImplementedError


def candidato(n: int, url: str, texto: str, titulo: str) -> Candidate:
    return Candidate(
        id=f"p{n}",
        chunk_id=f"c{n}",
        doc_id=f"d{n}",
        url=url,
        title=titulo,
        section="personas",
        heading_path=titulo,
        text=texto,
        cosine_score=0.9,
        retrieval_rank=n,
        rerank_score=5.0 - n,
        rerank_rank=n,
    )


CANDIDATOS = [
    candidato(1, f"{B}/personas/creditos/vivienda", "El crédito de vivienda financia…", "Vivienda"),
    candidato(2, f"{B}/personas/creditos/vivienda/requisitos", "Requisitos: …", "Requisitos"),
]


class RetrieverFalso(Retriever):
    """Retriever que devuelve candidatos fijos y registra las consultas que recibe."""

    def __init__(self, store: VectorStore | None = None) -> None:
        super().__init__(
            embedder=FakeEmbedder(), store=store or StoreFalso(), reranker=NoOpReranker()
        )
        self.consultas: list[str] = []
        self.no_answer = False
        self.calentado = False

    def warm_up(self) -> None:
        self.calentado = True

    def retrieve(self, query: str, *, section: str | None = None) -> RetrievalResult:
        self.store.count()  # falla como Qdrant si el almacén está "caído"
        self.consultas.append(query)
        resultados = [] if self.no_answer else list(CANDIDATOS)
        return RetrievalResult(
            query=query,
            section=section,
            reranker="falso",
            top_k=20,
            results=resultados,
            candidates=resultados,
            top_score=None if self.no_answer else 4.0,
            min_score=1.6,
            no_answer=self.no_answer,
            retrieval_ms=12.0,
            rerank_ms=300.0,
        )


class LLMGuionado(FakeLLMProvider):
    """LLM falso: reformula con `REESCRITA` y responde citando [1] y [2].
    `falla_respuesta=True` simula un error del LLM al generar la respuesta."""

    def __init__(self) -> None:
        super().__init__(self._responder)
        self.falla_respuesta = False

    @staticmethod
    def es_reformulacion(mensajes: Sequence[Message]) -> bool:
        return mensajes[0]["content"] == REWRITE_SYSTEM_PROMPT

    def _responder(self, mensajes: Sequence[Message]) -> str:
        if self.es_reformulacion(mensajes):
            return REESCRITA
        if self.falla_respuesta:
            raise LLMError("Se agotó el cupo diario del LLM. Intente mañana.", detail="429 PerDay")
        return "Necesita ser mayor de edad [2] y tener ingresos demostrables [1]."

    def llamadas_de_respuesta(self) -> list[list[Message]]:
        return [c for c in self.calls if not self.es_reformulacion(c)]

    def llamadas_de_reformulacion(self) -> list[list[Message]]:
        return [c for c in self.calls if self.es_reformulacion(c)]


def ajustes(**cambios: object) -> Settings:
    base: dict[str, object] = {
        "llm_provider": "fake",
        "embedding_provider": "fake",
        "history_window_n": 6,
        "query_rewrite_mode": "history_only",
    }
    base.update(cambios)
    return Settings(**base)  # type: ignore[arg-type]


def servicio(
    *,
    settings: Settings | None = None,
    repo: ConversationRepository | None = None,
    llm: LLMGuionado | None = None,
    retriever: RetrieverFalso | None = None,
) -> RAGService:
    """RAGService con dobles, armado por la misma fábrica que usa la API."""
    return ComponentFactory(settings or ajustes()).create_rag_service(
        repository=repo or InMemoryConversationRepository(),
        retriever=retriever or RetrieverFalso(),
        llm=llm or LLMGuionado(),
    )
