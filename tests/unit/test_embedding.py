"""Pruebas de embeddings (M4). Las del modelo real están marcadas `slow`."""

from pathlib import Path

import numpy as np
import pytest

from rag_bbva.config import Settings, get_settings
from rag_bbva.indexing.embedding import (
    PREFIJO_CONSULTA,
    PREFIJO_PASAJE,
    FakeEmbedder,
    SentenceTransformerEmbedder,
    is_model_cached,
)
from rag_bbva.indexing.factory import ComponentFactory

TARJETAS = (
    "Tarjetas de crédito Bancolombia: compra con tu tarjeta de crédito, paga en cuotas "
    "y acumula puntos con cada compra."
)
CDT = (
    "Un CDT es un certificado de depósito a término: inviertes tu plata a un plazo fijo "
    "y recibes una tasa de interés."
)


# ---------------------------------------------------------------- FakeEmbedder


def test_fake_embedder_determinista_y_normalizado() -> None:
    embedder = FakeEmbedder(dimension=64)

    a = embedder.embed_documents([TARJETAS, CDT])
    b = embedder.embed_documents([TARJETAS, CDT])

    assert a.shape == (2, 64)
    assert np.array_equal(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)
    assert embedder.embed_query("tarjeta").shape == (64,)
    assert embedder.embed_documents([]).shape == (0, 64)


def test_fake_embedder_textos_parecidos_quedan_cerca() -> None:
    embedder = FakeEmbedder()
    consulta = embedder.embed_query("tarjetas de crédito y compras en cuotas")
    docs = embedder.embed_documents([TARJETAS, CDT])

    assert float(docs[0] @ consulta) > float(docs[1] @ consulta)


def test_fake_embedder_cuenta_tokens() -> None:
    assert FakeEmbedder().count_tokens("hola mundo") == 5  # passage, hola, mundo + 2 especiales
    assert FakeEmbedder(max_tokens=10).max_tokens == 10


# ---------------------------------------------------------------- fábrica


def test_factory_crea_embedder_falso_o_real_sin_cargar_el_modelo(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean_env.setenv("EMBEDDING_PROVIDER", "fake")
    assert isinstance(ComponentFactory(Settings(_env_file=None)).create_embedder(), FakeEmbedder)

    clean_env.setenv("EMBEDDING_PROVIDER", "sentence_transformers")
    clean_env.setenv("MODEL_CACHE_DIR", str(tmp_path / "modelos"))
    clean_env.setenv("EMBEDDING_BATCH_SIZE", "8")
    embedder = ComponentFactory(Settings(_env_file=None)).create_embedder()

    assert isinstance(embedder, SentenceTransformerEmbedder)
    assert embedder.model_name == "intfloat/multilingual-e5-small"
    assert embedder.cache_dir == tmp_path / "modelos"
    assert embedder.batch_size == 8
    assert embedder._modelo is None  # carga perezosa: crear no descarga nada


def test_prefijos_e5() -> None:
    assert (PREFIJO_CONSULTA, PREFIJO_PASAJE) == ("query: ", "passage: ")


# ---------------------------------------------------------------- modelo real (slow)


def test_is_model_cached(tmp_path: Path) -> None:
    assert not is_model_cached("org/modelo", tmp_path)
    snapshot = tmp_path / "models--org--modelo" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    assert not is_model_cached("org/modelo", tmp_path)  # descarga incompleta
    (snapshot / "modules.json").write_text("[]")
    assert is_model_cached("org/modelo", tmp_path)


@pytest.fixture(scope="module")
def modelo_real() -> SentenceTransformerEmbedder:
    """Modelo real desde la caché local; si no está, los tests `slow` se saltan para no
    depender de la red (se descarga con `python -m rag_bbva.cli chunk` o el primer uso)."""
    ajustes = get_settings()
    if not is_model_cached(ajustes.embedding_model, ajustes.model_cache_dir):
        pytest.skip(
            f"El modelo {ajustes.embedding_model} no está en la caché local "
            f"(MODEL_CACHE_DIR={ajustes.model_cache_dir}); se omite para no descargarlo "
            "desde la red. Ejecute `python -m rag_bbva.cli chunk` una vez para bajarlo."
        )
    return SentenceTransformerEmbedder(ajustes.embedding_model, ajustes.model_cache_dir)


@pytest.mark.slow
def test_modelo_real_dimension_y_normalizacion(modelo_real: SentenceTransformerEmbedder) -> None:
    vectores = modelo_real.embed_documents([TARJETAS, CDT])

    assert modelo_real.dimension == 384
    assert vectores.shape == (2, 384)
    assert np.allclose(np.linalg.norm(vectores, axis=1), 1.0, atol=1e-5)
    assert np.isclose(np.linalg.norm(modelo_real.embed_query("hola")), 1.0, atol=1e-5)


@pytest.mark.slow
def test_modelo_real_tarjeta_mas_cerca_de_tarjetas_que_de_cdt(
    modelo_real: SentenceTransformerEmbedder,
) -> None:
    consulta = modelo_real.embed_query("tarjeta de crédito")
    tarjetas, cdt = modelo_real.embed_documents([TARJETAS, CDT])

    assert float(tarjetas @ consulta) > float(cdt @ consulta)


@pytest.mark.slow
def test_modelo_real_tokens(modelo_real: SentenceTransformerEmbedder) -> None:
    assert modelo_real.max_tokens == 512
    assert 3 < modelo_real.count_tokens("hola mundo") < 10
