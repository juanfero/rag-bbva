"""Conversión simple de HTML a texto con estructura markdown.

Se usa como *fallback* cuando trafilatura extrae poco: títulos como `#`, listas como
`- ` y tablas simples como filas `| a | b |`. No pretende cubrir todo HTML, solo la
estructura útil para el chunking por secciones (M4).
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


def _es_tabla_de_maquetacion(tabla: Tag) -> bool:
    """Tabla usada para maquetar: sus celdas contienen bloques (párrafos, títulos,
    listas u otras tablas). Se recorre como contenido normal, no como filas."""
    return tabla.find(["td", "th"]) is not None and any(
        celda.find(["p", "div", "ul", "ol", "table", *_TITULOS]) is not None
        for celda in tabla.find_all(["td", "th"])
    )


def _tabla(tabla: Tag) -> str | None:
    """Tabla simple como filas markdown; la primera fila hace de encabezado."""
    filas = []
    for fila in tabla.find_all("tr"):
        celdas = [_texto(c).replace("|", "/") for c in fila.find_all(["th", "td"])]
        if any(celdas):
            filas.append(celdas)
    if not filas:
        return None
    ancho = max(len(f) for f in filas)
    filas = [f + [""] * (ancho - len(f)) for f in filas]
    lineas = ["| " + " | ".join(filas[0]) + " |", "|" + " --- |" * ancho]
    lineas += ["| " + " | ".join(f) + " |" for f in filas[1:]]
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
