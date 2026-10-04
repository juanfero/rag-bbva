"""Estado compartido por las páginas de la interfaz (chat y Métricas)."""

import streamlit as st

from rag_bbva.config import get_settings
from rag_bbva.ui.api_client import ApiClient


def api_client() -> ApiClient:
    """Cliente de la API de la sesión. Los tests ponen uno falso en
    `st.session_state["api_client"]` antes de ejecutar la página."""
    if "api_client" not in st.session_state:
        ajustes = get_settings()
        st.session_state.api_client = ApiClient(
            ajustes.api_base_url, timeout=ajustes.ui_request_timeout_seconds
        )
    return st.session_state.api_client  # type: ignore[no-any-return]
