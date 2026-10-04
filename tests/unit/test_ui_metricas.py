"""Página "Métricas" de la interfaz (M11) con AppTest y un ApiClient falso: sin red."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from rag_bbva.analytics.service import AnalyticsService
from rag_bbva.exceptions import ApiClientError
from rag_bbva.memory.repository import InMemoryConversationRepository

from .test_analytics import _config, _historial

PAGINA = str(
    Path(__file__).resolve().parents[2] / "src" / "rag_bbva" / "ui" / "pages" / "1_Métricas.py"
)


class ApiFalsa:
    def __init__(self, resumen: dict[str, Any] | ApiClientError) -> None:
        self.resumen = resumen
        self.pedidos: list[date | None] = []

    def analytics_summary(self, since: date | None = None) -> dict[str, Any]:
        self.pedidos.append(since)
        if isinstance(self.resumen, ApiClientError):
            raise self.resumen
        return self.resumen


def _resumen(fuente: str, repo: InMemoryConversationRepository | None = None) -> dict[str, Any]:
    servicio = AnalyticsService(repository=repo or _historial(), settings=_config(), source=fuente)
    return servicio.summary().model_dump(mode="json")


def _pagina(api: ApiFalsa) -> AppTest:
    at = AppTest.from_file(PAGINA, default_timeout=30)
    at.session_state["api_client"] = api
    return at.run()


def _metricas(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def test_muestra_kpis_secciones_y_supuestos() -> None:
    at = _pagina(ApiFalsa(_resumen("history.db")))
    assert not at.exception
    assert at.title[0].value == "Métricas del historial"
    kpis = _metricas(at)
    assert kpis["Conversaciones"] == "4" and kpis["Turnos (preguntas respondidas)"] == "5"
    assert kpis["Sin información suficiente"] == "60.0 %"
    assert kpis["Horas ahorradas (estimadas)"] == "0.08 h"
    assert kpis["Costo estimado"] == "US$ 0.0023"
    textos = "\n".join(x.value for x in at.markdown) + "\n".join(x.value for x in at.caption)
    assert "corte duro sin LLM (score < -3.0) 1 (20.0 %)" in textos
    assert "Supuesto: Estimación: cada consulta resuelta ahorra MANUAL_SEARCH_MINUTES" in textos
    assert [s.value for s in at.subheader] == [
        "Resumen",
        "Operación",
        "Calidad",
        "Contenido",
        "Memoria, costo e impacto",
    ]
    assert not at.warning  # no es la base de demostración


def test_base_de_demostracion_se_avisa() -> None:
    at = _pagina(ApiFalsa(_resumen("demo.db")))
    assert at.warning and at.warning[0].value.startswith("**Datos de demostración.**")


def test_historial_vacio_sin_errores() -> None:
    at = _pagina(ApiFalsa(_resumen("history.db", InMemoryConversationRepository())))
    assert not at.exception
    assert _metricas(at)["Turnos (preguntas respondidas)"] == "0"
    assert any("Todavía no hay conversaciones" in x.value for x in at.info)


def test_error_de_la_api_es_amigable() -> None:
    at = _pagina(ApiFalsa(ApiClientError("No se pudo conectar con la API en http://x.")))
    assert not at.exception
    assert at.error[0].value.startswith("No se pudieron cargar las métricas. No se pudo conectar")


@pytest.mark.parametrize("filtrar", [False, True])
def test_filtro_por_fecha(filtrar: bool) -> None:
    api = ApiFalsa(_resumen("history.db"))
    at = _pagina(api)
    if filtrar:
        at.sidebar.checkbox[0].check().run()
        assert isinstance(api.pedidos[-1], date)
    else:
        assert api.pedidos == [None]
