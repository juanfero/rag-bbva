"""Tablas como filas markdown (M10): extracción con rowspan/colspan, celdas vecinas
iguales, chequeo de alineación del reporte y chunking que repite el encabezado.

Fixtures: recortes reales del crawl de M3 (tabla de tasas del CDT para empresas y dos
tablas del tarifario de cuentas, una con rowspan="7")."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from rag_bbva.indexing.chunking import HeadingAwareChunker, split_table, split_text_with_tables
from rag_bbva.processing.markdown import has_data_table, html_to_markdown, table_grid
from rag_bbva.processing.models import CleanDocument, RawPage
from rag_bbva.processing.pipeline import CleaningPipeline
from rag_bbva.processing.quality import markdown_tables, row_cells, table_report

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"
B = "https://www.bancolombia.com"


def _main(nombre: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / nombre).read_text("utf-8"), "lxml")


def _tablas(texto: str) -> list[list[str]]:
    return markdown_tables(texto)


def _limpiar(nombre: str, url: str) -> CleanDocument:
    pagina = RawPage(
        url=url,
        source_url=url,
        path=f"pages/{nombre}",
        fetched_at="2026-10-02T00:00:00+00:00",
        html=(FIXTURES / nombre).read_text("utf-8"),
    )
    doc = CleaningPipeline.default(min_chars=0, min_extraction_coverage=0.9).clean(pagina)
    assert isinstance(doc, CleanDocument), doc
    return doc


# --- Extracción ----------------------------------------------------------------------------


def test_tabla_cdt_conserva_celdas_vecinas_iguales_y_encabezados() -> None:
    texto = html_to_markdown(_main("tabla_cdt_empresas.html").main)
    (tabla,) = _tablas(texto)

    assert tabla[0] == (
        "|  | 60 - 89 días | 90 -119 días | 120 - 179 días | 180 - 239 días | 240 - 359 días "
        "| 360 - 539 días | 540 - 720 días |"
    )
    assert tabla[1] == "|" + " --- |" * 8
    assert tabla[2] == (
        "| $1.000.000 hasta $9.999.999 | 0,15% | 7,60% | 8,15% | 8,55% | 8,75% | 8,75% | 8,85% |"
    )
    assert len(tabla) == 2 + 5
    assert all(row_cells(f) == 8 for f in tabla)


def test_pipeline_completo_no_fusiona_valores_repetidos() -> None:
    """Regresión del hallazgo de M10: la limpieza perdía el segundo "8,75%"."""
    doc = _limpiar("tabla_cdt_empresas.html", f"{B}/negocios/productos/inversiones/cdt")

    assert doc.extraction == "selector_tablas"
    assert "| 8,75% | 8,75% | 8,85% |" in doc.text
    assert "| 9,50% | 9,50% | 9,55% |" in doc.text
    assert table_report([doc]).misaligned_rows == 0


def test_tarifario_expande_rowspan_y_cada_precio_queda_bajo_su_plan() -> None:
    doc = _limpiar("tabla_tarifario_cuentas.html", f"{B}/personas/tarifario/cuentas")
    simple, retiros = _tablas(doc.text)

    assert simple[0].startswith("| Nombre Tarifa | Plan Cero Tarifas (sin IVA)* |")
    assert simple[3] == "| Tarjeta débito física adicional" + " | $ 2.100" * 6 + " | Mensual |"
    encabezado = retiros[0].strip("| ").split(" | ")
    assert encabezado[:3] == ["Nombre Tarifa", "Canales", "Plan Cero Tarifas (sin IVA)*"]
    filas = [f.strip("| ").split(" | ") for f in retiros[2:]]
    assert len(filas) == 5 and all(len(f) == 8 for f in filas)
    # El rowspan="7" se repite en cada fila, y los precios no se corren de columna.
    assert {f[0] for f in filas} == {"Retirar dinero con Tarjeta Débito"}
    sucursales = next(f for f in filas if f[1] == "Sucursales Físicas Bancolombia")
    assert (
        dict(zip(encabezado, sucursales, strict=True))["Plan Cero Tarifas (sin IVA)*"]
        == "$ 11.490 por retiro"
    )
    assert (
        dict(zip(encabezado, sucursales, strict=True))["Plan Nómina Cero Tarifas (sin IVA)*"]
        == "$ 11.490 por retiro"
    )
    assert table_report([doc]).misaligned_rows == 0


def test_colspan_y_tabla_sin_encabezado() -> None:
    html = """<table><tr><td>Monto</td><td colspan="2">Hasta $1.000</td></tr>
              <tr><td>Retiros</td><td>Gratis</td><td>Gratis</td></tr></table>"""
    tabla = BeautifulSoup(html, "lxml").table
    assert table_grid(tabla) == [
        ["Monto", "Hasta $1.000", "Hasta $1.000"],
        ["Retiros", "Gratis", "Gratis"],
    ]
    texto = html_to_markdown(BeautifulSoup(f"<div>{html}</div>", "lxml").div)
    assert (
        texto == "| Monto | Hasta $1.000 | Hasta $1.000 |\n| Retiros | Gratis | Gratis |"
    )  # sin separador


def test_tabla_de_una_fila_es_una_linea_de_texto() -> None:
    html = (
        "<div><table><tr><td>Pago de facturas</td><td>$30.000.000 por día</td></tr></table></div>"
    )
    assert (
        html_to_markdown(BeautifulSoup(html, "lxml").div)
        == "Pago de facturas · $30.000.000 por día"
    )


def test_tabla_de_maquetacion_se_recorre_como_contenido() -> None:
    html = (
        "<div><table><tr><td><h3>Requisitos</h3>"
        "<ul><li>Ser mayor de edad</li></ul></td></tr></table></div>"
    )
    div = BeautifulSoup(html, "lxml").div
    assert not has_data_table(div)
    assert html_to_markdown(div) == "### Requisitos\n\n- Ser mayor de edad"


def test_celdas_con_p_y_div_siguen_siendo_tabla_de_datos() -> None:
    assert has_data_table(_main("tabla_cdt_empresas.html").main)


# --- Chequeo del reporte -------------------------------------------------------------------


def test_reporte_detecta_filas_desalineadas() -> None:
    texto = (
        "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n| 4 | 5 |"
        "\n\ntexto\n\n| x | y |\n| z | w |"
    )
    doc = CleanDocument(
        doc_id="d",
        url=f"{B}/x",
        title="t",
        section="personas",
        breadcrumbs=[],
        text=texto,
        html_lang="es",
        lang="es",
        lang_source="detectado",
        lastmod=None,
        published_at=None,
        scraped_at="2026-10-02T00:00:00+00:00",
        content_hash="h",
        n_chars=len(texto),
        template="A_main",
        extraction="selector_tablas",
    )
    reporte = table_report([doc])
    assert (reporte.tables, reporte.rows, reporte.misaligned_rows) == (2, 5, 1)
    assert reporte.cases[0].row == "| 4 | 5 |" and reporte.cases[0].expected == 3


# --- Chunking ------------------------------------------------------------------------------


def test_split_table_repite_el_encabezado_y_no_corta_filas() -> None:
    encabezado = "| Monto | Tasa |\n| --- | --- |"
    filas = [f"| Fila {i:02d} con su monto | {i},00% |" for i in range(30)]
    piezas = split_table("\n".join([encabezado, *filas]), size=200)

    assert len(piezas) > 3
    for pieza in piezas:
        lineas = pieza.split("\n")
        assert "\n".join(lineas[:2]) == encabezado
        assert all(linea in filas for linea in lineas[2:])
    assert [linea for p in piezas for linea in p.split("\n")[2:]] == filas  # todas, en orden


def test_split_text_with_tables_sin_tablas_es_igual_que_antes() -> None:
    from rag_bbva.indexing.chunking import split_text

    texto = "Párrafo uno. " * 80 + "\n\n" + "Párrafo dos. " * 80
    assert split_text_with_tables(texto, 400, 60) == split_text(texto, 400, 60)


def test_chunker_parte_la_tabla_del_tarifario_con_encabezado_en_cada_chunk() -> None:
    doc = _limpiar("tabla_tarifario_cuentas.html", f"{B}/personas/tarifario/cuentas")
    chunks = HeadingAwareChunker(chunk_size=800, chunk_overlap=120).chunk(doc)

    con_tabla = [c for c in chunks if "| Retirar dinero con Tarjeta Débito |" in c.text]
    assert len(con_tabla) >= 2  # la tabla de retiros no cabe en un chunk
    filas_vistas = []
    for chunk in con_tabla:
        (tabla,) = [t for t in markdown_tables(chunk.text) if "Canales" in t[0]]
        assert tabla[0].startswith("| Nombre Tarifa | Canales |") and tabla[1].startswith("| --- |")
        assert all(row_cells(f) == 8 for f in tabla)
        filas_vistas += tabla[2:]
    assert len(filas_vistas) == 5  # ninguna fila perdida ni partida


@pytest.mark.parametrize("nombre", ["tabla_cdt_empresas.html", "tabla_tarifario_cuentas.html"])
def test_fixtures_sin_filas_desalineadas_tras_chunking(nombre: str) -> None:
    doc = _limpiar(nombre, f"{B}/{nombre}")
    for chunk in HeadingAwareChunker(chunk_size=800, chunk_overlap=120).chunk(doc):
        for tabla in markdown_tables(chunk.text):
            datos = [f for f in tabla if "---" not in f]
            assert len({row_cells(f) for f in datos}) == 1, chunk.text
