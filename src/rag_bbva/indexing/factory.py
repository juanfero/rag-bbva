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
from rag_bbva.retrieval.reranker import CrossEncoderReranker, NoOpReranker, Reranker
from rag_bbva.retrieval.retriever import Retriever

_CHUNKERS: dict[str, type[ChunkingStrategy]] = {
    HeadingAwareChunker.name: HeadingAwareChunker,
    FixedSizeChunker.name: FixedSizeChunker,
}


class ComponentFactory:
    """Crea chunker, embedder, caché, almacén vectorial, reranker y retriever desde
    `Settings`."""

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
