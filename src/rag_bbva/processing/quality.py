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
