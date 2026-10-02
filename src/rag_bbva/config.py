"""Configuración externalizada del proyecto.

Toda la configuración se lee de variables de entorno (o de `.env`) y se valida con
pydantic-settings. El resto del código debe obtenerla siempre con `get_settings()`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LLMProviderName = Literal["xai", "fake"]


class Settings(BaseSettings):
    """Variables de configuración del sistema (ver `docs/00_VISION_GENERAL.md §7`)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Scraping
    target_base_url: HttpUrl = HttpUrl("https://www.bancolombia.com/")
    crawl_max_pages: int = Field(default=1200, gt=0)
    crawl_max_depth: int = Field(default=1, ge=0)
    crawl_delay_seconds: float = Field(default=1.0, ge=0)
    crawl_user_agent: str = Field(default="RAG-BBVA-TechTest/1.0", min_length=1)
    crawl_timeout_seconds: float = Field(default=20, gt=0)

    # Chunking y embeddings
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)
    embedding_model: str = "intfloat/multilingual-e5-small"

    # Base vectorial
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = Field(default="bancolombia_docs", min_length=1)

    # Recuperación y reranking
    retrieval_top_k: int = Field(default=20, gt=0)
    reranker_enabled: bool = True
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    rerank_top_n: int = Field(default=5, gt=0)

    # LLM (Grok vía API compatible con OpenAI)
    llm_provider: LLMProviderName = "xai"
    xai_api_key: SecretStr | None = None
    xai_base_url: str = "https://api.x.ai/v1"
    llm_model: str = "grok-4.7"
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_max_tokens: int = Field(default=800, gt=0)
    llm_timeout_seconds: float = Field(default=60, gt=0)

    # Historial y analítica
    history_db_path: Path = Path("data/history/history.db")
    history_window_n: int = Field(default=6, ge=0)
    manual_search_minutes: float = Field(default=5, ge=0)

    # Observabilidad
    log_level: LogLevel = "INFO"

    @model_validator(mode="after")
    def _validar_coherencia(self) -> Self:
        """Valida reglas que involucran más de un campo."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) debe ser menor que "
                f"CHUNK_SIZE ({self.chunk_size})"
            )
        if self.rerank_top_n > self.retrieval_top_k:
            raise ValueError(
                f"RERANK_TOP_N ({self.rerank_top_n}) no puede superar "
                f"RETRIEVAL_TOP_K ({self.retrieval_top_k})"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración cargada una sola vez (singleton vía caché)."""
    return Settings()
