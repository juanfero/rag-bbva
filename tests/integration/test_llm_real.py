"""Prueba contra la API real del LLM configurado (Gemini por defecto, ADR-012).

Marcada `integration`: hace una llamada mínima y se salta si no hay clave del
proveedor (en `.env` o en el entorno)."""

import pytest

from rag_bbva.config import get_settings
from rag_bbva.indexing.factory import ComponentFactory

pytestmark = pytest.mark.integration


def test_llm_responde_en_espanol_con_tokens() -> None:
    ajustes = get_settings()
    clave = ajustes.gemini_api_key if ajustes.llm_provider == "gemini" else ajustes.xai_api_key
    if ajustes.llm_provider == "fake" or clave is None or not clave.get_secret_value().strip():
        pytest.skip(
            f"Sin clave para LLM_PROVIDER={ajustes.llm_provider}: no se llama a la API real"
        )
    proveedor = ComponentFactory(ajustes).create_llm()

    respuesta = proveedor.complete(
        [{"role": "user", "content": "Responde solo con la palabra: hola"}], max_tokens=10
    )

    assert "hola" in respuesta.text.lower()
    assert respuesta.prompt_tokens > 0 and respuesta.completion_tokens > 0
    assert ajustes.llm_model in proveedor.list_models()
