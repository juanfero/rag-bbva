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
LLMProviderName = Literal["gemini", "xai", "fake"]
ChunkingStrategyName = Literal["heading_aware", "fixed_size"]
EmbeddingProviderName = Literal["sentence_transformers", "fake"]
QueryRewriteMode = Literal["off", "history_only", "always"]


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
    crawl_max_retries: int = Field(default=3, ge=0)
    crawl_backoff_seconds: float = Field(default=2.0, ge=0)
    crawl_block_threshold: int = Field(default=5, gt=0)
    # Prefijos de ruta que no se piden (ADR-010: la sala de prensa redirige a otro host).
    crawl_exclude_path_prefixes: list[str] = Field(
        default_factory=lambda: ["/acerca-de/sala-prensa/"]
    )
    raw_data_dir: Path = Path("data/raw")

    # Limpieza (M3)
    clean_data_dir: Path = Path("data/clean")
    clean_min_chars: int = Field(default=200, ge=0)
    clean_min_extraction_coverage: float = Field(default=0.9, ge=0, le=1)

    # Chunking y embeddings
    chunks_data_dir: Path = Path("data/chunks")
    chunking_strategy: ChunkingStrategyName = "heading_aware"
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)
    chunk_min_chars: int = Field(default=100, ge=0)
    embedding_provider: EmbeddingProviderName = "sentence_transformers"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_batch_size: int = Field(default=32, gt=0)
    model_cache_dir: Path = Path("models")

    # Base vectorial
    # Por defecto, el Qdrant del compose publicado en la máquina local; dentro de la red
    # de Docker (M12) se usa QDRANT_URL=http://qdrant:6333.
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = Field(default="bancolombia_docs", min_length=1)
    qdrant_timeout_seconds: float = Field(default=10, gt=0)
    qdrant_batch_size: int = Field(default=128, gt=0)
    embeddings_cache_dir: Path = Path("data/embeddings")

    # Recuperación y reranking
    retrieval_top_k: int = Field(default=20, gt=0)
    reranker_enabled: bool = True
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    reranker_max_length: int = Field(default=512, gt=0)
    reranker_batch_size: int = Field(default=16, gt=0)
    rerank_top_n: int = Field(default=5, gt=0)
    # Máximo de chunks de un mismo documento en el top-n (0 = sin límite).
    rerank_max_chunks_per_doc: int = Field(default=2, ge=0)
    # Score mínimo del reranker (top-1) para responder; por debajo: "sin información
    # suficiente". Calibrado con eval/calibration.jsonl: 27/30 aciertos (M06.md §6).
    rerank_min_score: float = 1.6
    # Umbral duro (ADR-016): por debajo, "sin información" sin llamar al LLM (fuera de
    # dominio). Entre este y RERANK_MIN_SCORE (zona gris) se llama al LLM, que puede
    # abstenerse con la marca [SIN_INFO]. Elegido con eval/calibration.jsonl: todas las
    # respondibles quedan por encima (mínima -2,58) y 8 de 15 no respondibles por debajo.
    rerank_hard_min_score: float = -3.0

    # LLM vía API compatible con OpenAI: Gemini por defecto (ADR-012), Grok como
    # alternativa (ADR-003). Las claves son secretas: solo en .env.
    llm_provider: LLMProviderName = "gemini"
    gemini_api_key: SecretStr | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    xai_api_key: SecretStr | None = None
    xai_base_url: str = "https://api.x.ai/v1"
    llm_model: str = "gemini-2.5-flash"
    # Modelo de respaldo ante un 429 del principal (cupo diario agotado). Vacío lo
    # desactiva. Debe ser del mismo proveedor (con xai, un modelo de Grok o vacío).
    llm_fallback_model: str = "gemini-3.1-flash-lite"
    # Solo Gemini: "none" apaga el razonamiento interno de gemini-2.5-flash (menos
    # latencia y tokens); "low" | "medium" | "high" lo activan; vacío no se envía.
    llm_reasoning_effort: str = "none"
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_max_tokens: int = Field(default=800, gt=0)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    # Reintentos propios (tenacity) solo ante 429, 5xx, timeouts y fallos de conexión;
    # los reintentos internos del SDK openai se desactivan (max_retries=0).
    llm_max_retries: int = Field(default=2, ge=0)
    llm_backoff_seconds: float = Field(default=1.0, ge=0)
    # Precio pago por millón de tokens (USD) para estimar el costo equivalente por
    # consulta. Fuente: https://ai.google.dev/gemini-api/docs/pricing, gemini-2.5-flash,
    # texto (actualizado 2026-10-01). Con la clave gratuita de AI Studio el costo real es 0.
    llm_price_input_per_mtok: float = Field(default=0.30, ge=0)
    llm_price_output_per_mtok: float = Field(default=2.50, ge=0)
    # Reformulación de la pregunta antes de recuperar: off | history_only | always.
    query_rewrite_mode: QueryRewriteMode = "history_only"
    query_rewrite_max_tokens: int = Field(default=120, gt=0)

    # Historial y analítica
    history_db_path: Path = Path("data/history/history.db")
    history_window_n: int = Field(default=6, ge=0)
    manual_search_minutes: float = Field(default=5, ge=0)

    # API (M9). En Docker (M12) se usa API_HOST=0.0.0.0.
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, gt=0, le=65535)
    # Largo máximo de una pregunta en caracteres (más largo → 422).
    chat_question_max_chars: int = Field(default=1000, gt=0)

    # Interfaz web (M10): consume la API por HTTP en API_BASE_URL.
    api_base_url: str = "http://127.0.0.1:8000"
    ui_host: str = "127.0.0.1"
    ui_port: int = Field(default=8501, gt=0, le=65535)
    # Espera máxima de la UI por una respuesta de la API. Alta a propósito: con el LLM
    # gratuito saturado, un turno real llegó a tardar ~150 s (M09.md §10.1).
    ui_request_timeout_seconds: float = Field(default=180, gt=0)

    # Observabilidad
    log_level: LogLevel = "INFO"

    @model_validator(mode="after")
    def _validar_coherencia(self) -> Self:
        """Valida reglas que involucran más de un campo."""
        if invalidos := [p for p in self.crawl_exclude_path_prefixes if not p.startswith("/")]:
            raise ValueError(
                f"CRAWL_EXCLUDE_PATH_PREFIXES debe tener rutas que empiecen por '/': {invalidos}"
            )
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) debe ser menor que "
                f"CHUNK_SIZE ({self.chunk_size})"
            )
        if self.rerank_hard_min_score > self.rerank_min_score:
            raise ValueError(
                f"RERANK_HARD_MIN_SCORE ({self.rerank_hard_min_score}) no puede superar "
                f"RERANK_MIN_SCORE ({self.rerank_min_score})"
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
