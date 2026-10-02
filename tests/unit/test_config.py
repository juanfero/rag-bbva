"""Pruebas de la configuración externalizada (M0)."""

import logging
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from rag_bbva.config import Settings, get_settings


def test_settings_defaults(clean_env: pytest.MonkeyPatch) -> None:
    """Sin `.env` ni variables de entorno se cargan los defaults documentados."""
    settings = Settings(_env_file=None)

    assert str(settings.target_base_url) == "https://www.bancolombia.com/"
    assert settings.crawl_max_pages == 1200
    assert settings.crawl_max_depth == 1
    assert settings.crawl_delay_seconds == 1.0
    assert settings.crawl_user_agent == "RAG-BBVA-TechTest/1.0"
    assert settings.crawl_timeout_seconds == 20
    assert settings.crawl_max_retries == 3
    assert settings.crawl_backoff_seconds == 2.0
    assert settings.crawl_block_threshold == 5
    assert settings.raw_data_dir == Path("data/raw")
    assert settings.chunk_size == 800
    assert settings.chunk_overlap == 120
    assert settings.embedding_model == "intfloat/multilingual-e5-small"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.qdrant_collection == "bancolombia_docs"
    assert settings.retrieval_top_k == 20
    assert settings.reranker_enabled is True
    assert settings.reranker_model == "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    assert settings.rerank_top_n == 5
    assert settings.llm_provider == "xai"
    assert settings.xai_api_key is None
    assert settings.xai_base_url == "https://api.x.ai/v1"
    assert settings.llm_model == "grok-4.7"
    assert settings.llm_temperature == 0.1
    assert settings.llm_max_tokens == 800
    assert settings.llm_timeout_seconds == 60
    assert settings.history_db_path == Path("data/history/history.db")
    assert settings.history_window_n == 6
    assert settings.manual_search_minutes == 5
    assert settings.log_level == "INFO"


def test_settings_env_override(clean_env: pytest.MonkeyPatch) -> None:
    """Una variable de entorno sobrescribe el default."""
    clean_env.setenv("HISTORY_WINDOW_N", "3")
    clean_env.setenv("RERANKER_ENABLED", "false")

    settings = Settings(_env_file=None)

    assert settings.history_window_n == 3
    assert settings.reranker_enabled is False


def test_settings_env_file_override(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Los valores de un archivo `.env` sobrescriben los defaults."""
    env_file = tmp_path / ".env"
    env_file.write_text("CHUNK_SIZE=1000\nLOG_LEVEL=DEBUG\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.chunk_size == 1000
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("env", "fragmento"),
    [
        ({"CHUNK_SIZE": "100", "CHUNK_OVERLAP": "100"}, "CHUNK_OVERLAP"),
        ({"CHUNK_SIZE": "100", "CHUNK_OVERLAP": "150"}, "CHUNK_OVERLAP"),
        ({"HISTORY_WINDOW_N": "-1"}, "history_window_n"),
        ({"RETRIEVAL_TOP_K": "3", "RERANK_TOP_N": "5"}, "RERANK_TOP_N"),
        ({"LLM_PROVIDER": "ollama"}, "llm_provider"),
        ({"LOG_LEVEL": "VERBOSE"}, "log_level"),
        ({"CRAWL_MAX_PAGES": "0"}, "crawl_max_pages"),
        ({"CRAWL_TIMEOUT_SECONDS": "0"}, "crawl_timeout_seconds"),
        ({"CRAWL_MAX_RETRIES": "-1"}, "crawl_max_retries"),
        ({"CRAWL_BLOCK_THRESHOLD": "0"}, "crawl_block_threshold"),
    ],
)
def test_settings_validation(
    clean_env: pytest.MonkeyPatch, env: dict[str, str], fragmento: str
) -> None:
    """Valores inválidos lanzan un error de validación explícito."""
    for key, value in env.items():
        clean_env.setenv(key, value)

    with pytest.raises(ValidationError, match=fragmento):
        Settings(_env_file=None)


def test_settings_history_window_zero_is_valid(clean_env: pytest.MonkeyPatch) -> None:
    """N=0 es válido: significa conversar sin memoria."""
    clean_env.setenv("HISTORY_WINDOW_N", "0")

    assert Settings(_env_file=None).history_window_n == 0


def test_api_key_is_secret(clean_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """`XAI_API_KEY` es `SecretStr` y no se filtra en `repr`, `str`, dumps ni logs."""
    secreto = "xai-clave-super-secreta-123"
    clean_env.setenv("XAI_API_KEY", secreto)

    settings = Settings(_env_file=None)

    assert isinstance(settings.xai_api_key, SecretStr)
    assert settings.xai_api_key.get_secret_value() == secreto
    assert secreto not in repr(settings)
    assert secreto not in str(settings)
    assert secreto not in settings.model_dump_json()

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("rag_bbva.test").info("Configuración cargada: %s", settings)
        logging.getLogger("rag_bbva.test").info("Clave: %r", settings.xai_api_key)
    assert caplog.records
    assert secreto not in caplog.text


def test_get_settings_is_cached(clean_env: pytest.MonkeyPatch) -> None:
    """`get_settings()` devuelve siempre la misma instancia."""
    assert get_settings() is get_settings()
