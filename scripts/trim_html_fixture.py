"""Recorta una página cruda de `data/raw/` para usarla como fixture de pruebas (M3).

Conserva la estructura que importa a la limpieza (plantilla, cabecera, navegación,
pie, menús de portlet, bloques de relacionados, migas e iconos) y quita lo pesado:
scripts, estilos, imágenes, comentarios, atributos que no se usan y listas largas
(se dejan los primeros ítems).

Uso:
    python scripts/trim_html_fixture.py data/raw/pages/<sha1>.html tests/fixtures/html/x.html
"""

import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup, Comment

QUITAR = ("script", "style", "link", "noscript", "svg", "img", "picture", "source", "iframe")
ATRIBUTOS = {"class", "id", "role", "lang", "aria-hidden", "aria-label", "style", "property"}
META_CONSERVAR = {"article:published_time"}
MAX_ITEMS = 3
# Clases que usa la limpieza (o que identifican la zona de la página); el resto son
# utilidades de estilo (Tailwind/Bootstrap) que solo abultan el fixture.
CLASES_UTILES = re.compile(
    r"bc-icon|breadcrumb|wptheme|miniatura|lrp|modal|cookie|component-container|footer|menu"
)


def trim(html: str) -> str:
    """Devuelve el HTML recortado."""
    soup = BeautifulSoup(html, "lxml")
    for elemento in soup(QUITAR):
        elemento.decompose()
    for meta in soup.find_all("meta"):
        if meta.get("property") not in META_CONSERVAR and not meta.get("charset"):
            meta.decompose()
    for comentario in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comentario.extract()
    for lista in soup.find_all(["ul", "ol"]):
        for item in lista.find_all("li", recursive=False)[MAX_ITEMS:]:
            item.decompose()
    # Elementos vacíos (sin texto): no aportan a la limpieza y abultan el fixture.
    for elemento in reversed(soup.find_all(True)):
        if elemento.name not in ("html", "head", "body", "br", "meta", "title") and not (
            elemento.get_text(strip=True)
        ):
            elemento.decompose()
    for elemento in soup.find_all(True):
        elemento.attrs = {
            k: v
            for k, v in elemento.attrs.items()
            if k in ATRIBUTOS and not (k == "style" and "display:none" not in str(v))
        }
        clases = [c for c in elemento.get("class") or [] if CLASES_UTILES.search(c)]
        if clases:
            elemento["class"] = clases
        else:
            elemento.attrs.pop("class", None)
    texto = str(soup)
    return "\n".join(linea.rstrip() for linea in texto.splitlines() if linea.strip()) + "\n"


def main() -> None:
    """Punto de entrada de la línea de comandos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("origen", type=Path)
    parser.add_argument("destino", type=Path)
    args = parser.parse_args()
    args.destino.write_text(trim(args.origen.read_text("utf-8")), encoding="utf-8")


if __name__ == "__main__":
    main()
