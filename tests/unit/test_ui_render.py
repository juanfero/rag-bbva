"""Funciones de presentación de la interfaz (M10) y aislamiento del núcleo."""

import ast
from pathlib import Path

import pytest

from rag_bbva.exceptions import ApiClientError
from rag_bbva.ui.api_client import HealthStatus
from rag_bbva.ui.render import (
    AVISO,
    detail_lines,
    escape_markdown,
    friendly_error,
    health_lines,
    link_citations,
    source_lines,
)

FUENTES = [
    {"n": 1, "url": "https://www.bancolombia.com/a", "title": "Página A"},
    {"n": 2, "url": "https://www.bancolombia.com/b", "title": None},
]


def test_citas_como_enlaces_y_citas_sin_fuente_intactas() -> None:
    texto = link_citations("Dato uno [1] y dos [2][1]; inválida [7].", FUENTES)
    assert texto == (
        "Dato uno [\\[1\\]](https://www.bancolombia.com/a) y dos "
        "[\\[2\\]](https://www.bancolombia.com/b)[\\[1\\]](https://www.bancolombia.com/a); "
        "inválida [7]."
    )


def test_montos_con_signo_peso_no_se_vuelven_latex() -> None:
    assert escape_markdown("Entre $151.994 y $200.000") == r"Entre \$151.994 y \$200.000"
    assert r"\$151.994" in link_citations("Tarifa $151.994 + IVA [1]", FUENTES)


def test_lineas_de_fuentes_con_titulo_o_url() -> None:
    lineas = source_lines(FUENTES)
    assert lineas[0].startswith("**[1]** [Página A](https://www.bancolombia.com/a)")
    assert lineas[1].startswith(
        "**[2]** [https://www.bancolombia.com/b](https://www.bancolombia.com/b)"
    )


@pytest.mark.parametrize(
    ("error", "esperado"),
    [
        (ApiClientError("La conversación no existe", status=404), "No encontré esa conversación"),
        (
            ApiClientError(
                "Solicitud inválida",
                status=422,
                detail="question: La pregunta no puede estar vacía",
            ),
            "La pregunta no es válida: La pregunta no puede estar vacía",
        ),
        (
            ApiClientError("Se agotó el cupo diario.", status=503),
            "El asistente no está disponible en este momento. Se agotó el cupo diario.",
        ),
        (ApiClientError("No se pudo conectar con la API"), "No se pudo conectar con la API"),
    ],
)
def test_errores_amigables(error: ApiClientError, esperado: str) -> None:
    assert friendly_error(error).startswith(esperado)


def test_estado_del_servicio() -> None:
    salud = HealthStatus.model_validate(
        {
            "status": "degraded",
            "qdrant": {"status": "down", "detail": "Qdrant caído"},
            "sqlite": {"status": "ok"},
            "llm": {
                "status": "ok",
                "model": "gemini-2.5-flash",
                "fallback_model": "gemini-3.1-flash-lite",
            },
        }
    )
    assert health_lines(salud) == [
        ("Búsqueda (Qdrant)", False, "Qdrant caído"),
        ("Historial (SQLite)", True, ""),
        ("LLM", True, "gemini-2.5-flash (respaldo: gemini-3.1-flash-lite)"),
    ]


def test_modo_detalle() -> None:
    lineas = detail_lines(
        {
            "rewritten_query": "¿Requisitos del crédito de vivienda?",
            "gray_zone": True,
            "timings": {
                "rewrite": 750.0,
                "retrieval": 20.0,
                "rerank": 700.0,
                "llm": 1200.0,
                "total": 2700.0,
            },
            "tokens": {"prompt": 2000, "completion": 300},
            "model": "gemini-3.1-flash-lite",
        }
    )
    assert lineas[0] == "**Pregunta reformulada:** ¿Requisitos del crédito de vivienda?"
    assert "Zona gris" in lineas[1]
    assert (
        lineas[2]
        == "**Tiempos (ms):** rewrite 750 · retrieval 20 · rerank 700 · llm 1,200 · total 2,700"
    )
    assert lineas[3] == "**Tokens:** 2000 entrada + 300 salida"
    assert lineas[-1] == "**Modelo:** gemini-3.1-flash-lite"
    assert detail_lines({"model": None})[-1] == "**Modelo:** no se llamó al LLM"


def test_aviso_de_prototipo() -> None:
    assert AVISO == "Prototipo de prueba técnica. No es un canal oficial de Bancolombia."


NUCLEO = (
    "rag_bbva.services",
    "rag_bbva.retrieval",
    "rag_bbva.llm",
    "rag_bbva.indexing",
    "rag_bbva.memory",
    "rag_bbva.api",
    "rag_bbva.scraping",
    "rag_bbva.processing",
)


def test_la_ui_no_importa_el_nucleo() -> None:
    """La UI habla con el sistema solo por HTTP (ApiClient); no importa el núcleo."""
    carpeta = Path(__file__).resolve().parents[2] / "src" / "rag_bbva" / "ui"
    for archivo in carpeta.rglob("*.py"):  # incluye ui/pages (M11)
        for nodo in ast.walk(ast.parse(archivo.read_text("utf-8"))):
            modulos = (
                [a.name for a in nodo.names]
                if isinstance(nodo, ast.Import)
                else [nodo.module or ""]
                if isinstance(nodo, ast.ImportFrom)
                else []
            )
            for modulo in modulos:
                assert not modulo.startswith(NUCLEO), f"{archivo.name} importa {modulo}"


def test_shorten() -> None:
    from rag_bbva.ui.render import shorten

    assert shorten("  ¿Qué es\n un CDT?  ") == "¿Qué es un CDT?"
    assert shorten("a" * 100, 10) == "aaaaaaaaa…"
