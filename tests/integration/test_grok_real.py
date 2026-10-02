"""Prueba contra la API real de xAI (Grok). Marcada `integration`: gasta unos pocos
tokens y se salta si no hay XAI_API_KEY (en `.env` o en el entorno)."""

import pytest

from rag_bbva.config import get_settings
from rag_bbva.indexing.factory import ComponentFactory

pytestmark = pytest.mark.integration


def test_grok_responde_en_espanol_con_tokens() -> None:
    ajustes = get_settings()
    if ajustes.xai_api_key is None or not ajustes.xai_api_key.get_secret_value().strip():
        pytest.skip("Sin XAI_API_KEY: no se llama a la API real de xAI")
    proveedor = ComponentFactory(ajustes).create_llm()

    respuesta = proveedor.complete(
        [{"role": "user", "content": "Responde solo con la palabra: hola"}], max_tokens=10
    )

    assert "hola" in respuesta.text.lower()
    assert respuesta.prompt_tokens > 0 and respuesta.completion_tokens > 0
