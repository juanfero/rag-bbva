"""Fábrica de componentes de indexación (patrón Factory).

El resto del código pide un chunker o un embedder a `ComponentFactory` y recibe la
implementación que indique la configuración, sin importar clases concretas.
"""

from rag_bbva.config import Settings
from rag_bbva.exceptions import ConfigurationError
from rag_bbva.indexing.chunking import ChunkingStrategy, FixedSizeChunker, HeadingAwareChunker
from rag_bbva.indexing.embedding import Embedder, FakeEmbedder, SentenceTransformerEmbedder
from rag_bbva.indexing.embedding_cache import EmbeddingCache
from rag_bbva.indexing.vector_store import QdrantVectorStore, VectorStore
from rag_bbva.llm.generator import AnswerGenerator
from rag_bbva.llm.provider import (
    FakeLLMProvider,
    GeminiProvider,
    LLMProvider,
    OpenAICompatibleProvider,
    UnconfiguredLLMProvider,
    XaiGrokProvider,
)
from rag_bbva.llm.rewriter import QueryRewriter
from rag_bbva.memory.repository import ConversationRepository
from rag_bbva.memory.sql_repository import SqlAlchemyConversationRepository
from rag_bbva.retrieval.reranker import CrossEncoderReranker, NoOpReranker, Reranker
from rag_bbva.retrieval.retriever import Retriever
from rag_bbva.services.health import HealthChecker
from rag_bbva.services.rag_service import RAGService

_CHUNKERS: dict[str, type[ChunkingStrategy]] = {
    HeadingAwareChunker.name: HeadingAwareChunker,
    FixedSizeChunker.name: FixedSizeChunker,
}


class ComponentFactory:
    """Crea chunker, embedder, caché, almacén vectorial, reranker, retriever, LLM,
    reformulador, generador de respuestas, historial, servicio RAG y chequeo de salud
    desde `Settings`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_chunker(self, strategy: str | None = None) -> ChunkingStrategy:
        """Chunker de `CHUNKING_STRATEGY` (o de `strategy`, si se indica)."""
        nombre = strategy or self.settings.chunking_strategy
        if nombre not in _CHUNKERS:
            raise ConfigurationError(
                "Estrategia de chunking desconocida", detail=f"{nombre}; use {sorted(_CHUNKERS)}"
            )
        return _CHUNKERS[nombre](self.settings.chunk_size, self.settings.chunk_overlap)

    def create_embedder(self) -> Embedder:
        """Embedder de `EMBEDDING_PROVIDER`."""
        if self.settings.embedding_provider == "fake":
            return FakeEmbedder()
        return SentenceTransformerEmbedder(
            self.settings.embedding_model,
            cache_dir=self.settings.model_cache_dir,
            batch_size=self.settings.embedding_batch_size,
        )

    def create_embedding_cache(self) -> EmbeddingCache:
        """Caché de embeddings del modelo configurado (o del falso)."""
        modelo = (
            "fake" if self.settings.embedding_provider == "fake" else self.settings.embedding_model
        )
        return EmbeddingCache(self.settings.embeddings_cache_dir, modelo)

    def create_vector_store(self) -> VectorStore:
        """Almacén vectorial: Qdrant en `QDRANT_URL` (no conecta hasta usarse)."""
        return QdrantVectorStore.from_url(
            self.settings.qdrant_url,
            self.settings.qdrant_collection,
            timeout=self.settings.qdrant_timeout_seconds,
            batch_size=self.settings.qdrant_batch_size,
        )

    def create_reranker(self, enabled: bool | None = None) -> Reranker:
        """Cross-encoder si `RERANKER_ENABLED` (o `enabled`) es verdadero; si no, NoOp."""
        activo = self.settings.reranker_enabled if enabled is None else enabled
        if not activo:
            return NoOpReranker()
        return CrossEncoderReranker(
            self.settings.reranker_model,
            self.settings.model_cache_dir,
            max_length=self.settings.reranker_max_length,
            batch_size=self.settings.reranker_batch_size,
        )

    def create_retriever(
        self,
        *,
        rerank: bool | None = None,
        top_n: int | None = None,
        embedder: Embedder | None = None,
        store: VectorStore | None = None,
    ) -> Retriever:
        """Retriever con la configuración de `RETRIEVAL_TOP_K`, `RERANK_*`."""
        return Retriever(
            embedder=embedder or self.create_embedder(),
            store=store or self.create_vector_store(),
            reranker=self.create_reranker(rerank),
            top_k=self.settings.retrieval_top_k,
            top_n=top_n or self.settings.rerank_top_n,
            max_per_doc=self.settings.rerank_max_chunks_per_doc,
            min_score=self.settings.rerank_min_score,
        )

    def create_llm(self) -> LLMProvider:
        """Proveedor de `LLM_PROVIDER` (gemini | xai | fake). Exige la clave del proveedor
        al crearlo (ADR-006): falla aquí y no a mitad de una conversación."""
        ajustes = self.settings
        if ajustes.llm_provider == "fake":
            return FakeLLMProvider(model="fake-model")
        clase: type[OpenAICompatibleProvider]
        if ajustes.llm_provider == "gemini":
            clase, clave, base_url = GeminiProvider, ajustes.gemini_api_key, ajustes.gemini_base_url
            esfuerzo = ajustes.llm_reasoning_effort or None
        else:
            clase, clave, base_url = XaiGrokProvider, ajustes.xai_api_key, ajustes.xai_base_url
            esfuerzo = None
        if clave is None or not clave.get_secret_value().strip():
            raise ConfigurationError(
                f"Falta {clase.key_env} para usar LLM_PROVIDER={ajustes.llm_provider}. "
                "Agréguela en el archivo .env (ver .env.example) o use LLM_PROVIDER=fake "
                "para pruebas."
            )
        return clase(
            api_key=clave.get_secret_value(),
            base_url=base_url,
            model=ajustes.llm_model,
            temperature=ajustes.llm_temperature,
            max_tokens=ajustes.llm_max_tokens,
            timeout=ajustes.llm_timeout_seconds,
            max_retries=ajustes.llm_max_retries,
            backoff_seconds=ajustes.llm_backoff_seconds,
            reasoning_effort=esfuerzo,
        )

    def create_query_rewriter(self, llm: LLMProvider, mode: str | None = None) -> QueryRewriter:
        """Reformulador con `QUERY_REWRITE_MODE` (o `mode`)."""
        return QueryRewriter(
            llm,
            mode=mode or self.settings.query_rewrite_mode,
            max_tokens=self.settings.query_rewrite_max_tokens,
        )

    def create_answer_generator(self, llm: LLMProvider) -> AnswerGenerator:
        """Generador de respuestas con citas."""
        return AnswerGenerator(llm, max_tokens=self.settings.llm_max_tokens)

    def create_conversation_repository(self) -> ConversationRepository:
        """Historial de conversaciones en SQLite (`HISTORY_DB_PATH`)."""
        return SqlAlchemyConversationRepository.from_path(self.settings.history_db_path)

    def create_rag_service(
        self,
        *,
        repository: ConversationRepository | None = None,
        retriever: Retriever | None = None,
        llm: LLMProvider | None = None,
        tolerate_missing_llm: bool = False,
    ) -> RAGService:
        """Servicio RAG completo. Con `tolerate_missing_llm`, si falta la clave del LLM se
        usa `UnconfiguredLLMProvider`: el servicio arranca, `/health` lo informa y cada
        pregunta que necesite el LLM responde con el error de configuración."""
        if llm is None:
            try:
                llm = self.create_llm()
            except ConfigurationError as exc:
                if not tolerate_missing_llm:
                    raise
                llm = UnconfiguredLLMProvider(exc)
        return RAGService(
            repository=repository or self.create_conversation_repository(),
            retriever=retriever or self.create_retriever(),
            rewriter=self.create_query_rewriter(llm),
            generator=self.create_answer_generator(llm),
            history_window_n=self.settings.history_window_n,
        )

    def create_health_checker(self, service: RAGService) -> HealthChecker:
        """Chequeo de salud sobre el almacén vectorial y el historial del servicio."""
        return HealthChecker(
            store=service.retriever.store, repository=service.repository, settings=self.settings
        )
