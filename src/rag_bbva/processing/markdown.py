"""Conversión simple de HTML a texto con estructura markdown.

Se usa como *fallback* cuando trafilatura extrae poco, y siempre que la página tiene
tablas de datos (M10): títulos como `#`, listas como `- ` y tablas como filas
`| a | b |`. No pretende cubrir todo HTML, solo la estructura útil para el chunking por
secciones (M4).

Tablas (M10): cada fila conserva **todas** sus celdas, incluidas las vecinas con el
mismo valor ("8,75% | 8,75%"), y las celdas con `rowspan`/`colspan` se repiten en cada
fila y columna que ocupan, para que ningún valor quede bajo la columna equivocada.
"""

from bs4 import Comment, NavigableString, Tag

_TITULOS = {f"h{n}": n for n in range(1, 7)}
# Elementos de bloque: cortan el párrafo en curso y se recorren por dentro.
_BLOQUES = frozenset(
    {
        "address", "article", "blockquote", "body", "dd", "details", "dialog", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "form", "html", "li", "main", "p", "pre",
        "section", "summary",
    }
)  # fmt: skip


_CELDAS_Y_FILAS = frozenset({"tbody", "thead", "tfoot", "tr", "td", "th"})
_CONTENEDORES = [*_BLOQUES, *_TITULOS, "ul", "ol", "table"]


def _texto(elemento: Tag) -> str:
    """Texto del elemento en una sola línea, con espacios normalizados."""
    return " ".join(elemento.get_text(" ").split())


# Una celda con más texto que esto es contenido de página maquetado en tabla.
_MAX_CHARS_CELDA_DATOS = 300
# Tope de rowspan/colspan, por si el HTML trae valores absurdos.
_MAX_SPAN = 50
SEPARADOR_ENCABEZADO = "---"


def _es_tabla_de_maquetacion(tabla: Tag) -> bool:
    """Tabla usada para maquetar: sus celdas contienen bloques estructurales (títulos,
    listas u otras tablas) o mucho texto. Se recorre como contenido normal.

    Un `<p>` o un `<div>` dentro de una celda **no** la vuelve de maquetación (M10): las
    tablas de tasas y tarifas del sitio envuelven cada valor en `div > div > p`.
    """
    celdas = tabla.find_all(["td", "th"])
    return any(
        celda.find(["table", "ul", "ol", *_TITULOS]) is not None
        or len(_texto(celda)) > _MAX_CHARS_CELDA_DATOS
        for celda in celdas
    )


def _span(celda: Tag, atributo: str) -> int:
    try:
        return max(1, min(int(str(celda.get(atributo, 1))), _MAX_SPAN))
    except ValueError:
        return 1


def table_grid(tabla: Tag) -> list[list[str]]:
    """Celdas de la tabla en una cuadrícula: una celda con `rowspan`/`colspan` se repite
    en cada posición que ocupa. Todas las filas quedan con el mismo número de columnas
    (las que faltan en el HTML, al final, quedan vacías). Omite filas vacías."""
    filas_html = tabla.find_all("tr")
    ocupadas: dict[tuple[int, int], str] = {}
    for r, fila in enumerate(filas_html):
        c = 0
        for celda in fila.find_all(["th", "td"]):
            while (r, c) in ocupadas:
                c += 1
            texto = _texto(celda).replace("|", "/")
            filas_span, columnas_span = _span(celda, "rowspan"), _span(celda, "colspan")
            for dr in range(filas_span):
                if r + dr >= len(filas_html):
                    break
                for dc in range(columnas_span):
                    ocupadas[(r + dr, c + dc)] = texto
            c += columnas_span
    if not ocupadas:
        return []
    ancho = max(c for _, c in ocupadas) + 1
    cuadricula = [[ocupadas.get((r, c), "") for c in range(ancho)] for r in range(len(filas_html))]
    return [fila for fila in cuadricula if any(fila)]


def _tiene_encabezado(tabla: Tag) -> bool:
    """La primera fila es encabezado si la tabla usa `<th>`/`<thead>` o si todas las
    celdas con texto de esa fila están en negrita (como el tarifario)."""
    if tabla.find(["th", "thead"]) is not None:
        return True
    primera = tabla.find("tr")
    if primera is None:
        return False
    celdas = [c for c in primera.find_all(["td", "th"]) if _texto(c)]
    return bool(celdas) and all(
        _texto(c)
        == " ".join(" ".join(n.get_text(" ") for n in c.find_all(["strong", "b"])).split())
        for c in celdas
    )


def _fila_md(celdas: list[str]) -> str:
    return "| " + " | ".join(celdas) + " |"


def _tabla(tabla: Tag) -> str | None:
    """Tabla de datos como filas markdown. Con encabezado, la primera fila va seguida de
    la línea `| --- |`; sin encabezado, todas las filas son datos. Una tabla de una sola
    fila se escribe como una línea de texto (no hay columnas que alinear)."""
    filas = table_grid(tabla)
    if not filas:
        return None
    if len(filas) == 1:
        return " · ".join(c for c in filas[0] if c)
    lineas = [_fila_md(f) for f in filas]
    if _tiene_encabezado(tabla):
        lineas.insert(1, "|" + f" {SEPARADOR_ENCABEZADO} |" * len(filas[0]))
    return "\n".join(lineas)


def _lista(lista: Tag) -> str | None:
    """Ítems directos de una lista como `- texto`."""
    items = [_texto(li) for li in lista.find_all("li", recursive=False)]
    items = [i for i in items if i]
    return "\n".join(f"- {i}" for i in items) or None


def _recorrer(elemento: Tag, bloques: list[str]) -> None:
    buffer: list[str] = []

    def vaciar() -> None:
        texto = " ".join(" ".join(buffer).split())
        if texto:
            bloques.append(texto)
        buffer.clear()

    for hijo in elemento.children:
        if isinstance(hijo, Comment):
            continue
        if isinstance(hijo, NavigableString):
            buffer.append(str(hijo))
            continue
        if not isinstance(hijo, Tag):
            continue
        nombre = hijo.name
        if nombre in _TITULOS:
            vaciar()
            if texto := _texto(hijo):
                bloques.append(f"{'#' * _TITULOS[nombre]} {texto}")
        elif nombre in ("ul", "ol"):
            vaciar()
            if lista := _lista(hijo):
                bloques.append(lista)
        elif nombre == "table":
            vaciar()
            if _es_tabla_de_maquetacion(hijo):
                _recorrer(hijo, bloques)
            elif tabla := _tabla(hijo):
                bloques.append(tabla)
        elif nombre == "br":
            vaciar()
        elif nombre in _BLOQUES or nombre in _CELDAS_Y_FILAS or hijo.find(_CONTENEDORES):
            vaciar()
            _recorrer(hijo, bloques)
        else:
            buffer.append(hijo.get_text(" "))
    vaciar()


def html_to_markdown(elemento: Tag) -> str:
    """Convierte el contenido del elemento en bloques markdown separados por línea en blanco."""
    bloques: list[str] = []
    _recorrer(elemento, bloques)
    return "\n\n".join(bloques)


def has_data_table(elemento: Tag) -> bool:
    """¿El elemento contiene alguna tabla de datos de al menos dos filas?"""
    return any(
        not _es_tabla_de_maquetacion(tabla) and len(table_grid(tabla)) >= 2
        for tabla in elemento.find_all("table")
        if tabla.find_parent("table") is None
    )
