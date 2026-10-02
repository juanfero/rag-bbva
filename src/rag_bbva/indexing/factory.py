"""Fábrica de componentes de indexación (patrón Factory).

El resto del código pide un chunker o un embedder a `ComponentFactory` y recibe la
implementación que indique la configuración, sin importar clases concretas.
"""

from rag_bbva.config import Settings
from rag_bbva.exceptions import ConfigurationError
from rag_bbva.indexing.chunking import ChunkingStrategy, FixedSizeChunker, HeadingAwareChunker
from rag_bbva.indexing.embedding import Embedder, FakeEmbedder, SentenceTransformerEmbedder

_CHUNKERS: dict[str, type[ChunkingStrategy]] = {
    HeadingAwareChunker.name: HeadingAwareChunker,
    FixedSizeChunker.name: FixedSizeChunker,
}


class ComponentFactory:
    """Crea chunker y embedder a partir de `Settings`."""

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
