"""Punto de entrada de la interfaz (M14): navegación con nombres propios.

`python -m rag_bbva.cli ui` ejecuta este archivo. Declara las dos páginas con su
título, porque sin `st.navigation` Streamlit nombraría el chat según su archivo ("app").
Cada página sigue siendo un script propio (`app.py` y `pages/1_Métricas.py`).
"""

from pathlib import Path

import streamlit as st

CARPETA = Path(__file__).parent

navegacion = st.navigation(
    [
        st.Page(CARPETA / "app.py", title="Chat", icon="💬", default=True),
        st.Page(CARPETA / "pages" / "1_Métricas.py", title="Métricas", icon="📊"),
    ]
)
navegacion.run()
