"""Pruebas del análisis de HTML estático (M1, sin red)."""

from pathlib import Path

import pytest

from rag_bbva.scraping.page_analysis import (
    PageAnalysis,
    analyze_html,
    compare_texts,
    rendered_only_segments,
    text_segments,
    visible_text,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"


def _analizar(nombre: str) -> PageAnalysis:
    return analyze_html((FIXTURES / nombre).read_text(encoding="utf-8"))


def test_pagina_estatica_no_requiere_js() -> None:
    """Una página server-rendered con mucho texto no requiere JS."""
    analisis = _analizar("estatica.html")

    assert analisis.verdict == "no_requiere_js"
    assert analisis.title == "Cuenta de Ahorros | Banco"
    assert analisis.words > 150
    assert analisis.scripts == 2
    assert analisis.placeholders == ()
    assert analisis.spa_markers == ()


def test_pagina_estatica_detecta_contenedor_principal() -> None:
    """El contenedor con más texto y menos enlaces queda primero; nav/footer no."""
    analisis = _analizar("estatica.html")

    assert analisis.top_containers[0].selector in {"main#main-content", "article.producto.detalle"}
    assert analisis.top_containers[0].link_density == 0
    assert all("header" not in c.selector for c in analisis.top_containers)
    assert analisis.common_selectors["main"] > 1000
    assert analisis.common_selectors["#main-content"] > 1000
    assert analisis.common_selectors["[role=main]"] == 0


def test_cascaron_spa_requiere_js() -> None:
    """Un HTML casi vacío con marcadores de SPA requiere renderizar JS."""
    analisis = _analizar("spa.html")

    assert analisis.verdict == "requiere_js"
    assert "next.js" in analisis.spa_markers
    assert "#root vacío" in analisis.spa_markers
    assert analisis.noscript_text is not None
    assert "JavaScript" in analisis.noscript_text
    assert "habilitar" not in visible_text((FIXTURES / "spa.html").read_text(encoding="utf-8"))


def test_placeholders_sin_resolver_marcan_parcial() -> None:
    """Texto suficiente pero con ${...} sin resolver → dependencia parcial de JS."""
    analisis = _analizar("portal_placeholders.html")

    assert analisis.verdict == "parcial"
    assert analisis.placeholders == ("${title}", "${loading}")
    assert analisis.top_containers[0].selector.startswith("div.")


def test_visible_text_excluye_scripts_y_estilos() -> None:
    """El texto visible no incluye código JS ni CSS y normaliza espacios."""
    texto = visible_text("<p>Hola\n\n  mundo</p><script>var x=1;</script><style>.a{}</style>")

    assert texto == "Hola mundo"


@pytest.mark.parametrize(
    ("estatico", "renderizado", "cobertura", "requiere"),
    [
        ("cuenta de ahorros sin cuota", "cuenta de ahorros sin cuota", 1.0, False),
        ("cuenta", "cuenta ahorros tasa interés", 0.25, True),
        ("algo", "", 1.0, False),
    ],
)
def test_compare_texts(estatico: str, renderizado: str, cobertura: float, requiere: bool) -> None:
    """La cobertura es la fracción del vocabulario renderizado presente en el estático."""
    comparacion = compare_texts(estatico, renderizado)

    assert comparacion.coverage == cobertura
    assert comparacion.requires_js is requiere


def test_compare_texts_muestra_palabras_solo_renderizadas() -> None:
    """Se listan (ordenadas) palabras que solo aparecen tras renderizar."""
    comparacion = compare_texts("cuenta", "cuenta tasa interés")

    assert comparacion.only_rendered_sample == ("interés", "tasa")


def test_text_segments_filtra_cortos_y_duplicados() -> None:
    """Solo fragmentos largos, sin repetir y sin texto de scripts."""
    html = (
        "<p>Corto</p><p>Este es un fragmento suficientemente largo para contar.</p>"
        "<div>Este es un fragmento suficientemente largo para contar.</div>"
        "<script>var texto = 'un script larguísimo que no es contenido visible';</script>"
    )

    assert text_segments(html) == ["Este es un fragmento suficientemente largo para contar."]


def test_rendered_only_segments() -> None:
    """Devuelve lo que el renderizado agrega respecto al HTML estático."""
    estatico = "<p>Texto principal del producto con todas sus condiciones.</p>"
    renderizado = estatico + "<div>Usamos cookies para mejorar tu experiencia en el sitio.</div>"

    assert rendered_only_segments(estatico, renderizado) == (
        "Usamos cookies para mejorar tu experiencia en el sitio.",
    )
