"""Control de calidad de la limpieza: "fugas" de boilerplate en el texto limpio.

Cada patrón detecta un texto que solo aparece en navegación, pie, menús de portlet,
iconos, banners o bloques de relacionados del sitio, nunca en el contenido propio.
La meta es 0 fugas en los fixtures; en la corrida real se reportan conteo y casos.
"""

import re
from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel

from rag_bbva.processing.models import CleanDocument

LEAK_PATTERNS: dict[str, re.Pattern[str]] = {
    "placeholder": re.compile(r"\$\{|\{\{"),
    "menu_portlet": re.compile(r"Component Action Menu|Display portlet menu|Deferred Modules"),
    "error_wcm": re.compile(
        r"Invalid configuration found|Contact the administrator|"
        r"This Web Content Viewer is associated|cannot display the referenced content"
    ),
    "icono": re.compile(r"\b(arrow2-(?:down|up|left|right)|check-small|menu-dots-v)\b"),
    "relacionados": re.compile(
        r"(?im)^\W*(contenido relacionado|art[ií]culos relacionados|te puede interesar)\W*$"
    ),
    "venta_cruzada": re.compile(
        r"Descubre otros canales que te van a interesar|"
        r"Si te gustó este producto, estos te van a interesar"
    ),
    "interfaz": re.compile(r"(?i)link copiado en porta ?papeles"),
    "cookies": re.compile(r"(?i)(usamos|utilizamos) cookies|aceptar (todas las )?cookies"),
    # Frases tal como aparecen en el pie. "Bancolombia S.A. Establecimiento Bancario",
    # la línea 01 8000 o la dirección sueltas también aparecen en avisos legales y
    # textos de contacto del contenido propio, así que no cuentan como fuga.
    "pie": re.compile(
        r"Copyright ©|BANCOLOMBIA S\.A\. Establecimiento Bancario|"
        r"Carrera 48 # 26 - 85 Medellín \u2013 Colombia|Línea gratuita resto del país|"
        r"Síguenos en nuestras redes sociales"
    ),
    "menu_sitio": re.compile(
        r"Sucursal Virtual Personas Sucursal Virtual Negocios|Ver más Entrar|"
        r"Negocios especializados Negocios en Colombia|Personas Negocios Corporativos|"
        r"Saltar al contenido"
    ),
}
_CONTEXTO = 40
MAX_CASOS = 50
_SEPARADOR_TABLA = re.compile(r"^\|(\s*-{3,}\s*\|)+$")


class LeakCase(BaseModel):
    """Una fuga encontrada en un documento."""

    url: str
    pattern: str
    snippet: str


class LeakReport(BaseModel):
    """Resumen de fugas sobre un conjunto de documentos."""

    total: int
    documents_with_leaks: int
    by_pattern: dict[str, int]
    cases: list[LeakCase]


def find_leaks(text: str) -> dict[str, list[str]]:
    """Fragmentos de texto que coinciden con cada patrón de fuga."""
    hallazgos: dict[str, list[str]] = {}
    for nombre, patron in LEAK_PATTERNS.items():
        for m in patron.finditer(text):
            inicio, fin = max(0, m.start() - _CONTEXTO), min(len(text), m.end() + _CONTEXTO)
            hallazgos.setdefault(nombre, []).append(" ".join(text[inicio:fin].split()))
    return hallazgos


def leak_report(documents: Iterable[CleanDocument], max_cases: int = MAX_CASOS) -> LeakReport:
    """Cuenta fugas por patrón y conserva hasta `max_cases` casos de ejemplo."""
    por_patron: Counter[str] = Counter()
    casos: list[LeakCase] = []
    con_fugas = 0
    for doc in documents:
        hallazgos = find_leaks(doc.text)
        if hallazgos:
            con_fugas += 1
        for nombre, fragmentos in hallazgos.items():
            por_patron[nombre] += len(fragmentos)
            casos.extend(LeakCase(url=doc.url, pattern=nombre, snippet=f) for f in fragmentos)
    return LeakReport(
        total=sum(por_patron.values()),
        documents_with_leaks=con_fugas,
        by_pattern=dict(sorted(por_patron.items())),
        cases=casos[:max_cases],
    )


class MisalignedRow(BaseModel):
    """Fila de tabla con un número de celdas distinto al del encabezado."""

    url: str
    expected: int
    found: int
    row: str


class TableReport(BaseModel):
    """Tablas markdown del texto limpio y filas desalineadas (meta: 0, M10)."""

    documents_with_tables: int
    tables: int
    rows: int
    # Filas con un número de celdas distinto al de la primera fila de su tabla.
    misaligned_rows: int
    # Líneas que empiezan con `|` pero no cierran con `|` (fila cortada o mal formada).
    malformed_rows: int = 0
    cases: list[MisalignedRow]


def markdown_tables(text: str) -> list[list[str]]:
    """Tablas markdown del texto: cada una es su lista de filas `| … |` (incluida la
    línea separadora del encabezado, si la hay)."""
    tablas: list[list[str]] = []
    actual: list[str] = []
    for linea in [*text.split("\n"), ""]:
        limpia = linea.strip()
        if limpia.startswith("|") and limpia.endswith("|") and len(limpia) > 1:
            actual.append(limpia)
            continue
        if actual:
            tablas.append(actual)
            actual = []
    return tablas


def row_cells(fila: str) -> int:
    """Número de celdas de una fila markdown `| a | b |`."""
    return len(fila.strip()[1:-1].split("|"))


def table_report(documents: Iterable[CleanDocument], max_cases: int = MAX_CASOS) -> TableReport:
    """Cuenta tablas y filas cuyo número de celdas difiere del de la primera fila
    (el encabezado, o la primera fila de datos en tablas sin encabezado)."""
    con_tablas = tablas = filas = 0
    casos: list[MisalignedRow] = []
    desalineadas = mal_formadas = 0
    for doc in documents:
        for linea in doc.text.split("\n"):
            limpia = linea.strip()
            if limpia.startswith("|") and not (limpia.endswith("|") and len(limpia) > 1):
                mal_formadas += 1
                casos.append(MisalignedRow(url=doc.url, expected=-1, found=-1, row=limpia[:200]))
        encontradas = markdown_tables(doc.text)
        con_tablas += bool(encontradas)
        for tabla in encontradas:
            tablas += 1
            datos = [f for f in tabla if not _SEPARADOR_TABLA.match(f)]
            esperado = row_cells(datos[0])
            for fila in datos:
                filas += 1
                if (encontrado := row_cells(fila)) != esperado:
                    desalineadas += 1
                    casos.append(
                        MisalignedRow(
                            url=doc.url, expected=esperado, found=encontrado, row=fila[:200]
                        )
                    )
    return TableReport(
        documents_with_tables=con_tablas,
        tables=tablas,
        rows=filas,
        misaligned_rows=desalineadas,
        malformed_rows=mal_formadas,
        cases=casos[:max_cases],
    )
