"""Analítica del historial (M11): cada métrica contra valores calculados a mano.

Historial fixture (horas en UTC; Bogotá = UTC-5):
- C1, 2 turnos (2026-10-03 15:00 y 15:05 UTC = 10:00 y 10:05 Bogotá):
  T1 "¿Qué es un CDT?" responde con 2 fuentes, 👍. T2 "¿y su plazo?" reformulada, 1 fuente, 👎.
- C2, 1 turno (23:30 UTC = 18:30 Bogotá): "receta de arepas", corte duro (score -6, sin LLM).
- C3, 1 turno (2026-10-04 13:00 UTC = 08:00): otra entidad, el LLM se abstiene con score 5,3
  (fuera de la zona gris), 1 fuente.
- C4, 1 turno (13:30 UTC = 08:30): "Receta de arepas!", abstención en la zona gris (-1,0).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rag_bbva.analytics.metrics import (
    COLUMNAS_TURNOS,
    group_questions,
    percentile,
    section_of,
)
from rag_bbva.analytics.privacy import mask_numbers
from rag_bbva.analytics.service import AnalyticsService
from rag_bbva.memory.models import MessageMetrics
from rag_bbva.memory.repository import InMemoryConversationRepository

from .fakes_rag import ajustes

B = "https://www.bancolombia.com"


class Reloj:
    def __init__(self) -> None:
        self.ahora = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.ahora


def _config(**cambios: object):  # type: ignore[no-untyped-def]
    return ajustes(
        rerank_hard_min_score=-3.0,
        rerank_min_score=1.6,
        manual_search_minutes=5,
        llm_price_input_per_mtok=0.30,
        llm_price_output_per_mtok=2.50,
        analytics_timezone="America/Bogota",
        analytics_top_n=10,
        **cambios,
    )


def _historial() -> InMemoryConversationRepository:
    reloj = Reloj()
    repo = InMemoryConversationRepository(reloj)
    m = MessageMetrics
    t1 = repo.add_turn(
        None,
        "¿Qué es un CDT?",
        "Un CDT es… [1][2]",
        sources=[{"n": 1, "url": f"{B}/personas/cdt"}, {"n": 2, "url": f"{B}/acerca-de/glosario"}],
        metrics=m(
            rewrite_ms=0.0,
            retrieval_ms=20,
            rerank_ms=800,
            llm_ms=1000,
            total_ms=2000,
            top_score=7.0,
            no_answer=False,
            prompt_tokens=1000,
            completion_tokens=100,
            model="m",
            gray_zone=False,
        ),
    )
    repo.set_feedback(t1.answer.id, "up")
    reloj.ahora += timedelta(minutes=5)
    t2 = repo.add_turn(
        t1.conversation.id,
        "¿y su plazo?",
        "El plazo… [1]",
        sources=[{"n": 1, "url": f"{B}/personas/cdt"}],
        metrics=m(
            rewrite_ms=500,
            rewritten_query="¿Cuál es el plazo de un CDT?",
            retrieval_ms=30,
            rerank_ms=900,
            llm_ms=1500,
            total_ms=3000,
            top_score=5.0,
            no_answer=False,
            prompt_tokens=1500,
            completion_tokens=200,
            model="m",
            gray_zone=False,
        ),
    )
    repo.set_feedback(t2.answer.id, "down")
    reloj.ahora = datetime(2026, 10, 3, 23, 30, tzinfo=UTC)
    repo.add_turn(
        None,
        "receta de arepas",
        "No encontré información suficiente…",
        metrics=m(
            rewrite_ms=0.0,
            retrieval_ms=10,
            rerank_ms=700,
            llm_ms=0.0,
            total_ms=900,
            top_score=-6.0,
            no_answer=True,
            prompt_tokens=0,
            completion_tokens=0,
            gray_zone=False,
        ),
    )
    reloj.ahora = datetime(2026, 10, 4, 13, 0, tzinfo=UTC)
    repo.add_turn(
        None,
        "¿Banco de Bogotá?",
        "Solo tengo información de Bancolombia… [1]",
        sources=[{"n": 1, "url": f"{B}/personas/cuentas/ahorros"}],
        metrics=m(
            rewrite_ms=0.0,
            retrieval_ms=15,
            rerank_ms=750,
            llm_ms=1200,
            total_ms=2500,
            top_score=5.3,
            no_answer=True,
            prompt_tokens=1200,
            completion_tokens=50,
            model="m",
            gray_zone=False,
        ),
    )
    reloj.ahora = datetime(2026, 10, 4, 13, 30, tzinfo=UTC)
    repo.add_turn(
        None,
        "Receta de arepas!",
        "No encontré esa información…",
        metrics=m(
            rewrite_ms=0.0,
            retrieval_ms=12,
            rerank_ms=600,
            llm_ms=800,
            total_ms=1500,
            top_score=-1.0,
            no_answer=True,
            prompt_tokens=900,
            completion_tokens=30,
            model="m",
            gray_zone=True,
        ),
    )
    return repo


@pytest.fixture
def resumen():  # type: ignore[no-untyped-def]
    return AnalyticsService(
        repository=_historial(), settings=_config(), source="history.db"
    ).summary()


def test_operativas(resumen) -> None:  # type: ignore[no-untyped-def]
    o = resumen.operational
    assert (o.conversations, o.messages, o.turns) == (4, 10, 5)
    assert (o.turns_per_conversation.mean, o.turns_per_conversation.median) == (
        1.25,
        1.0,
    )  # [2,1,1,1]
    assert o.turns_by_day == {"2026-10-03": 3, "2026-10-04": 2}  # en hora de Bogotá
    assert o.turns_by_hour == {"08": 2, "10": 2, "18": 1}
    lat = o.latency_ms
    assert (lat["total"].n, lat["total"].p50, lat["total"].p95) == (
        5,
        2000,
        3000,
    )  # [900,1500,2000,2500,3000]
    assert (lat["rewrite"].n, lat["rewrite"].p50) == (1, 500)  # solo el turno reformulado
    assert (lat["retrieval"].p50, lat["retrieval"].p95) == (15, 30)  # [10,12,15,20,30]
    assert (lat["rerank"].p50, lat["rerank"].p95) == (750, 900)  # [600,700,750,800,900]
    assert (lat["llm"].n, lat["llm"].p50, lat["llm"].p95) == (4, 1000, 1500)  # sin el corte duro


def test_calidad_separa_corte_duro_y_abstencion(resumen) -> None:  # type: ignore[no-untyped-def]
    q = resumen.quality
    assert (q.no_answer.count, q.no_answer.pct) == (3, 60.0)
    assert (q.hard_cut.count, q.hard_cut.pct) == (1, 20.0)  # arepas, -6 < -3
    assert (q.llm_abstention.count, q.llm_abstention.pct) == (2, 40.0)
    assert (q.llm_abstention_gray_zone, q.llm_abstention_outside_gray_zone) == (1, 1)
    assert (q.gray_zone_turns.count, q.with_sources.count, q.with_sources.pct) == (1, 3, 60.0)
    assert q.reranker_top_score_mean == 2.06  # (7 + 5 - 6 + 5,3 - 1) / 5
    f = q.feedback
    assert (f.up, f.down, f.voted, f.coverage_pct, f.up_rate_pct) == (1, 1, 2, 40.0, 50.0)


def test_contenido(resumen) -> None:  # type: ignore[no-untyped-def]
    c = resumen.content
    assert [(i.name, i.count) for i in c.top_urls] == [
        (f"{B}/personas/cdt", 2),
        (f"{B}/acerca-de/glosario", 1),
        (f"{B}/personas/cuentas/ahorros", 1),
    ]
    assert [(i.name, i.count) for i in c.top_sections] == [("personas", 3), ("acerca-de", 1)]
    # Sin embedder se agrupa por texto normalizado; se usa la pregunta autónoma.
    assert [(g.question, g.count) for g in c.frequent_questions] == [
        ("receta de arepas", 2),
        ("¿Qué es un CDT?", 1),
        ("¿Cuál es el plazo de un CDT?", 1),
        ("¿Banco de Bogotá?", 1),
    ]
    assert c.frequent_questions[0].examples == ["Receta de arepas!"]
    assert [(g.question, g.count) for g in c.content_gaps] == [
        ("receta de arepas", 2),
        ("¿Banco de Bogotá?", 1),
    ]


def test_memoria_costo_e_impacto(resumen) -> None:  # type: ignore[no-untyped-def]
    mem = resumen.memory
    assert (mem.multi_turn_conversations.count, mem.multi_turn_conversations.pct) == (1, 25.0)
    assert (mem.rewritten_turns.count, mem.rewritten_turns.pct, mem.turns_with_rewrite_data) == (
        1,
        20.0,
        5,
    )
    c = resumen.cost
    assert (c.prompt_tokens, c.completion_tokens, c.total_tokens, c.tokens_per_turn) == (
        4600,
        380,
        4980,
        996.0,
    )
    assert c.estimated_cost_usd == 0.00233  # (4600 · 0,30 + 380 · 2,50) / 1e6
    assert c.estimated_cost_per_turn_usd == 0.000466
    i = resumen.impact
    # Resuelta = respondida (no_answer falso) y sin 👎: solo T1.
    assert (i.resolved_turns, i.resolution_rate_pct, i.hours_saved) == (1, 20.0, 0.08)  # 1 · 5 / 60
    assert i.cost_per_resolved_usd == 0.00233
    assert "supuesto" in i.assumption and "MANUAL_SEARCH_MINUTES" in i.assumption


def test_meta_y_datos_de_demostracion() -> None:
    normal = AnalyticsService(
        repository=_historial(), settings=_config(), source="history.db"
    ).summary()
    demo = AnalyticsService(repository=_historial(), settings=_config(), source="demo.db").summary()
    assert normal.meta.demo is False and demo.meta.demo is True
    assert normal.meta.rerank_hard_min_score == -3.0


def test_since_filtra_por_fecha() -> None:
    servicio = AnalyticsService(repository=_historial(), settings=_config(), source="h")
    r = servicio.summary(since=datetime(2026, 10, 4, 0, 0, tzinfo=UTC))
    assert (r.operational.conversations, r.operational.turns) == (2, 2)
    assert r.quality.no_answer.count == 2


def test_historial_vacio_da_ceros_sin_errores() -> None:
    r = AnalyticsService(
        repository=InMemoryConversationRepository(), settings=_config(), source="h"
    ).summary()
    o, q = r.operational, r.quality
    assert (o.conversations, o.messages, o.turns, o.turns_per_conversation.mean) == (0, 0, 0, 0.0)
    assert o.turns_by_day == {} and o.latency_ms["total"].p50 is None
    assert (q.no_answer.pct, q.with_sources.pct, q.reranker_top_score_mean) == (0.0, 0.0, None)
    assert (q.feedback.coverage_pct, q.feedback.up_rate_pct) == (0.0, 0.0)
    assert r.content.top_urls == [] and r.content.frequent_questions == []
    assert (r.cost.total_tokens, r.cost.estimated_cost_per_turn_usd) == (0, 0.0)
    assert (r.impact.resolved_turns, r.impact.hours_saved, r.impact.cost_per_resolved_usd) == (
        0,
        0.0,
        None,
    )


def test_conversacion_sin_turnos_cuenta_con_cero() -> None:
    repo = InMemoryConversationRepository()
    repo.create_conversation()
    repo.add_message(repo.create_conversation().id, "user", "pregunta sin respuesta")
    r = AnalyticsService(repository=repo, settings=_config(), source="h").summary()
    assert (r.operational.conversations, r.operational.turns) == (
        1,
        0,
    )  # solo la que tiene mensajes


@pytest.mark.parametrize(
    ("valores", "p50", "p95"),
    [(list(range(1, 21)), 10, 19), ([5], 5, 5), ([3, 1, 2], 2, 3), ([], None, None)],
)
def test_percentil_por_rango_mas_cercano(
    valores: list[int], p50: float | None, p95: float | None
) -> None:
    assert (percentile(valores, 50), percentile(valores, 95)) == (p50, p95)


class EmbedderFijo:
    """Vectores fijos por texto: controla exactamente los cosenos."""

    def __init__(self, vectores: dict[str, list[float]]) -> None:
        self.vectores = vectores

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(self.vectores[text], dtype=float)


def test_agrupa_preguntas_casi_identicas_por_coseno() -> None:
    a, b = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    casi_a = [0.98, 0.2, 0.0]  # coseno con a ≈ 0,980
    lejos = [0.9, 0.44, 0.0]  # coseno con a ≈ 0,898 < 0,93
    embedder = EmbedderFijo(
        {"¿Qué es un CDT?": a, "que es un cdt": casi_a, "¿tasa del CDT?": lejos, "otra": b}
    )
    preguntas = ["¿Qué es un CDT?", "otra", "que es un cdt", "¿Qué es un CDT?", "¿tasa del CDT?"]
    grupos = group_questions(preguntas, embedder, umbral=0.93, top_n=10)  # type: ignore[arg-type]
    assert [(g.question, g.count) for g in grupos] == [
        ("¿Qué es un CDT?", 3),
        ("otra", 1),
        ("¿tasa del CDT?", 1),
    ]
    assert grupos[0].examples == ["que es un cdt"]
    assert len(group_questions(preguntas, embedder, umbral=0.99, top_n=10)) == 4  # type: ignore[arg-type]


def test_section_of() -> None:
    assert section_of(f"{B}/personas/cdt") == "personas"
    assert section_of(f"{B}/") == "inicio"


# --- Privacidad -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("mi cédula es 1020345678", "mi cédula es [número]"),
        ("cédula 1.020.345.678 y cuenta 123-456789-01", "cédula [número] y cuenta [número]"),
        ("llámame al 300 123 4567 o +57 3001234567", "llámame al [número] o [número]"),
        ("tarjeta 4111 1111 1111 1111", "tarjeta [número]"),
        ("clave123456", "clave[número]"),
        ("CDT de $5.000.000 a 90 días", "CDT de $5.000.000 a 90 días"),
        ("CDT de $ 5.000.000", "CDT de $ 5.000.000"),
        ("plazo de 90 días, año 2025, código 12345", "plazo de 90 días, año 2025, código 12345"),
        ("CDT de 5000000 pesos", "CDT de [número] pesos"),
    ],
)
def test_enmascara_numeros_largos(texto: str, esperado: str) -> None:
    assert mask_numbers(texto) == esperado


def test_la_analitica_muestra_las_preguntas_enmascaradas() -> None:
    repo = InMemoryConversationRepository()
    repo.add_turn(
        None,
        "mi cédula es 1020345678, ¿tengo CDT?",
        "No encontré…",
        metrics=MessageMetrics(no_answer=True, top_score=-5.0),
    )
    servicio = AnalyticsService(repository=repo, settings=_config(), source="h")
    r = servicio.summary()
    assert r.content.content_gaps[0].question == "mi cédula es [número], ¿tengo CDT?"
    assert "1020345678" not in servicio.turns().to_csv()


# --- Export ---------------------------------------------------------------------------------


def test_export_csv_con_columnas_esperadas(tmp_path: Path) -> None:
    servicio = AnalyticsService(repository=_historial(), settings=_config(), source="h")
    rutas = servicio.export(tmp_path, "csv")
    nombres = {r.name: r for r in rutas}
    assert set(nombres) == {
        "turnos.csv",
        "urls_citadas.csv",
        "secciones_citadas.csv",
        "preguntas_frecuentes.csv",
        "brechas_de_contenido.csv",
    }
    turnos = pd.read_csv(nombres["turnos.csv"])
    assert list(turnos.columns) == COLUMNAS_TURNOS and len(turnos) == 5
    assert set(turnos["no_answer_type"].fillna("")) == {"", "corte_duro", "abstencion_llm"}
    assert list(pd.read_csv(nombres["urls_citadas.csv"]).columns) == ["url", "citas"]
    assert list(pd.read_csv(nombres["secciones_citadas.csv"]).columns) == ["seccion", "citas"]
    for nombre in ("preguntas_frecuentes.csv", "brechas_de_contenido.csv"):
        assert list(pd.read_csv(nombres[nombre]).columns) == ["pregunta", "veces", "ejemplos"]


def test_export_json(tmp_path: Path) -> None:
    import json

    (ruta,) = AnalyticsService(repository=_historial(), settings=_config(), source="h").export(
        tmp_path, "json"
    )
    datos = json.loads(ruta.read_text("utf-8"))
    assert set(datos) == {"meta", "operational", "quality", "content", "memory", "cost", "impact"}
    assert datos["impact"]["resolved_turns"] == 1
