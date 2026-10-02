"""Análisis de HTML estático para decidir si hace falta renderizar JavaScript.

Se usa en la exploración del sitio (M1). Las heurísticas son deliberadamente simples
y explicables: cuánto texto trae el HTML sin ejecutar JS, si aparecen plantillas
sin resolver o marcadores de frameworks SPA, y qué contenedores concentran el texto.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from bs4 import BeautifulSoup, Tag

# Etiquetas cuyo contenido no es texto visible.
_NO_VISIBLES = ("script", "style", "noscript", "template", "svg", "iframe")
# Placeholders de plantillas no resueltas en el servidor: ${titulo}, {{ titulo }}.
_PLACEHOLDER = re.compile(r"\$\{[^}\s]{1,60}\}|\{\{[^}]{1,60}\}\}")
_PALABRA = re.compile(r"\w{3,}", re.UNICODE)
# Marcadores de aplicaciones renderizadas en el cliente.
_MARCADORES_SPA = {
    "next.js": "__NEXT_DATA__",
    "nuxt": "__NUXT__",
    "angular": "ng-version",
    "react": "data-reactroot",
    "vue": "data-v-app",
}
# Selectores habituales de contenido principal que se evalúan en cada página.
SELECTORES_COMUNES = (
    "main",
    "article",
    "[role=main]",
    "#main-content",
    "#content",
    ".main-content",
)
# Umbrales del veredicto (palabras visibles en el HTML estático).
MIN_PALABRAS_ESTATICO = 150
# Cobertura mínima del texto renderizado presente ya en el HTML estático.
MIN_COBERTURA = 0.8

Veredicto = Literal["no_requiere_js", "parcial", "requiere_js"]


@dataclass(frozen=True)
class Contenedor:
    """Elemento HTML candidato a contener el contenido principal."""

    selector: str
    chars: int
    link_density: float


@dataclass(frozen=True)
class PageAnalysis:
    """Resultado del análisis de una página estática."""

    title: str | None
    text_chars: int
    words: int
    scripts: int
    placeholders: tuple[str, ...]
    spa_markers: tuple[str, ...]
    noscript_text: str | None
    common_selectors: dict[str, int]
    top_containers: tuple[Contenedor, ...]
    verdict: Veredicto


@dataclass(frozen=True)
class TextComparison:
    """Comparación del texto estático frente al renderizado con JS."""

    static_words: int
    rendered_words: int
    coverage: float
    only_rendered_sample: tuple[str, ...]

    @property
    def requires_js(self) -> bool:
        """El renderizado aporta una parte relevante del texto."""
        return self.coverage < MIN_COBERTURA


def visible_text(html: str) -> str:
    """Extrae el texto visible (sin scripts, estilos ni plantillas) normalizado."""
    soup = BeautifulSoup(html, "lxml")
    for etiqueta in soup(_NO_VISIBLES):
        etiqueta.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def _palabras(texto: str) -> set[str]:
    return {p.lower() for p in _PALABRA.findall(texto)}


def _descriptor(elemento: Tag) -> str:
    """Selector CSS legible del elemento: `tag#id` o `tag.clase1.clase2`."""
    if elemento.get("id"):
        return f"{elemento.name}#{elemento['id']}"
    clases = [c for c in elemento.get("class", []) if c][:3]
    return elemento.name + "".join(f".{c}" for c in clases)


def _contenedores(soup: BeautifulSoup, top: int) -> tuple[Contenedor, ...]:
    """Contenedores con id/clase que concentran más texto con pocos enlaces."""
    candidatos: list[tuple[float, Contenedor]] = []
    for elemento in soup.find_all(["main", "article", "section", "div"]):
        if not (
            elemento.get("id") or elemento.get("class") or elemento.name in {"main", "article"}
        ):
            continue
        texto = re.sub(r"\s+", " ", elemento.get_text(" ")).strip()
        if len(texto) < 200:
            continue
        enlaces = sum(len(a.get_text(" ", strip=True)) for a in elemento.find_all("a"))
        densidad = round(enlaces / len(texto), 2)
        puntaje = len(texto) * (1 - densidad)
        candidatos.append((puntaje, Contenedor(_descriptor(elemento), len(texto), densidad)))
    candidatos.sort(key=lambda c: -c[0])

    vistos: set[str] = set()
    resultado: list[Contenedor] = []
    for _, contenedor in candidatos:
        if contenedor.selector in vistos:
            continue
        vistos.add(contenedor.selector)
        resultado.append(contenedor)
        if len(resultado) == top:
            break
    return tuple(resultado)


def _veredicto(words: int, placeholders: Iterable[str], spa: Iterable[str]) -> Veredicto:
    if words < MIN_PALABRAS_ESTATICO:
        return "requiere_js"
    if any(placeholders) or any(spa):
        return "parcial"
    return "no_requiere_js"


def analyze_html(html: str, top_containers: int = 5) -> PageAnalysis:
    """Analiza el HTML estático de una página (sin ejecutar JavaScript)."""
    soup = BeautifulSoup(html, "lxml")
    titulo = soup.title.get_text(strip=True) if soup.title else None
    scripts = len(soup.find_all("script"))
    noscript = " ".join(n.get_text(" ", strip=True) for n in soup.find_all("noscript")).strip()
    spa = tuple(nombre for nombre, marca in _MARCADORES_SPA.items() if marca in html)
    for vacio in ("root", "app"):
        nodo = soup.find(id=vacio)
        if isinstance(nodo, Tag) and not nodo.get_text(strip=True):
            spa += (f"#{vacio} vacío",)

    for etiqueta in soup(_NO_VISIBLES):
        etiqueta.decompose()
    texto = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    placeholders = tuple(dict.fromkeys(_PLACEHOLDER.findall(texto)))
    palabras = len(_PALABRA.findall(texto))

    selectores = {}
    for selector in SELECTORES_COMUNES:
        nodo = soup.select_one(selector)
        selectores[selector] = len(nodo.get_text(" ", strip=True)) if nodo else 0

    return PageAnalysis(
        title=titulo,
        text_chars=len(texto),
        words=palabras,
        scripts=scripts,
        placeholders=placeholders,
        spa_markers=spa,
        noscript_text=noscript or None,
        common_selectors=selectores,
        top_containers=_contenedores(soup, top_containers),
        verdict=_veredicto(palabras, placeholders, spa),
    )


def compare_texts(static_text: str, rendered_text: str, sample: int = 15) -> TextComparison:
    """Mide qué fracción del vocabulario renderizado ya está en el HTML estático."""
    estatico = _palabras(static_text)
    renderizado = _palabras(rendered_text)
    solo_render = renderizado - estatico
    cobertura = 1.0 if not renderizado else 1 - len(solo_render) / len(renderizado)
    return TextComparison(
        static_words=len(estatico),
        rendered_words=len(renderizado),
        coverage=round(cobertura, 3),
        only_rendered_sample=tuple(sorted(solo_render)[:sample]),
    )


def text_segments(html: str, min_chars: int = 40) -> list[str]:
    """Fragmentos de texto visible (nodos de texto normalizados) de al menos `min_chars`."""
    soup = BeautifulSoup(html, "lxml")
    for etiqueta in soup(_NO_VISIBLES):
        etiqueta.decompose()
    fragmentos = (re.sub(r"\s+", " ", t).strip() for t in soup.stripped_strings)
    return list(dict.fromkeys(f for f in fragmentos if len(f) >= min_chars))


def rendered_only_segments(
    static_html: str, rendered_html: str, limit: int = 8, min_chars: int = 40
) -> tuple[str, ...]:
    """Fragmentos de texto que solo existen tras renderizar con JavaScript."""
    estatico = visible_text(static_html)
    nuevos = [f for f in text_segments(rendered_html, min_chars) if f not in estatico]
    return tuple(nuevos[:limit])
