"""Servicio de analítica (M11): lee el historial por el Repository y calcula el resumen.

Lo usan la CLI (`metrics`), la API (`GET /analytics/summary`) y, a través de la API,
la página "Métricas" de la interfaz.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd

from rag_bbva.analytics.metrics import AnalyticsSummary, summarize, turns_frame
from rag_bbva.config import Settings
from rag_bbva.exceptions import HistoryError
from rag_bbva.indexing.embedding import Embedder
from rag_bbva.memory.repository import ConversationRepository

logger = logging.getLogger(__name__)

ExportFormat = Literal["csv", "json"]

# Archivos del export CSV y sus columnas.
COLUMNAS_EXPORT = {
    "turnos.csv": None,  # columnas de `metrics.COLUMNAS_TURNOS`
    "urls_citadas.csv": ["url", "citas"],
    "secciones_citadas.csv": ["seccion", "citas"],
    "preguntas_frecuentes.csv": ["pregunta", "veces", "ejemplos"],
    "brechas_de_contenido.csv": ["pregunta", "veces", "ejemplos"],
}


class AnalyticsService:
    """Analítica del historial de un repositorio."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        settings: Settings,
        embedder: Embedder | None = None,
        source: str = "",
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.embedder = embedder
        self.source = source

    def summary(self, since: datetime | None = None) -> AnalyticsSummary:
        """Resumen de métricas desde `since` (todo el historial si es `None`)."""
        mensajes = self.repository.all_messages(since=since)
        return summarize(
            mensajes, self.settings, embedder=self.embedder, since=since, source=self.source
        )

    def turns(self, since: datetime | None = None) -> pd.DataFrame:
        """Tabla de turnos (preguntas enmascaradas)."""
        return turns_frame(self.repository.all_messages(since=since), self.settings)

    def export(
        self, destino: Path, formato: ExportFormat, since: datetime | None = None
    ) -> list[Path]:
        """Escribe el export en `destino`: CSV de las tablas principales o JSON del resumen."""
        try:
            destino.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HistoryError("No se pudo crear la carpeta del export", detail=str(exc)) from exc
        resumen = self.summary(since)
        if formato == "json":
            ruta = destino / "resumen.json"
            ruta.write_text(
                json.dumps(resumen.model_dump(mode="json"), ensure_ascii=False, indent=2), "utf-8"
            )
            return [ruta]
        c = resumen.content
        tablas = {
            "turnos.csv": self.turns(since),
            "urls_citadas.csv": pd.DataFrame(
                [(i.name, i.count) for i in c.top_urls], columns=["url", "citas"]
            ),
            "secciones_citadas.csv": pd.DataFrame(
                [(i.name, i.count) for i in c.top_sections], columns=["seccion", "citas"]
            ),
            "preguntas_frecuentes.csv": pd.DataFrame(
                [(g.question, g.count, " | ".join(g.examples)) for g in c.frequent_questions],
                columns=["pregunta", "veces", "ejemplos"],
            ),
            "brechas_de_contenido.csv": pd.DataFrame(
                [(g.question, g.count, " | ".join(g.examples)) for g in c.content_gaps],
                columns=["pregunta", "veces", "ejemplos"],
            ),
        }
        rutas = []
        for nombre, tabla in tablas.items():
            ruta = destino / nombre
            tabla.to_csv(ruta, index=False, encoding="utf-8")
            rutas.append(ruta)
        logger.info("Export de analítica", extra={"archivos": [r.name for r in rutas]})
        return rutas
