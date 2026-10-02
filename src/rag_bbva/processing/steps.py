"""Pasos de la limpieza (patrón Chain of Responsibility).

Cada paso es una clase independiente que recibe un `WorkingDocument`, lo transforma y
lo pasa al siguiente eslabón, o corta la cadena devolviendo `Discarded` con el motivo.
Los selectores y textos de boilerplate salen de la exploración del sitio
(`docs/exploracion_sitio.md` §5) y del HTML real guardado en M2.
"""

import hashlib
import json
import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from typing import Self

import trafilatura
from bs4 import BeautifulSoup, Tag

from rag_bbva.exceptions import ProcessingError
from rag_bbva.processing.markdown import html_to_markdown
from rag_bbva.processing.models import Discarded, Template, WorkingDocument

logger = logging.getLogger(__name__)

# Contenedor de contenido principal de cada plantilla, en orden de preferencia.
SELECTORES_PLANTILLA: tuple[tuple[str, Template], ...] = (
    ("main", "A_main"),
    ("#main-content", "B_main_content"),
    ("[role=main]", "C_role_main"),
)

# Elementos que nunca son contenido: técnicos, navegación y estructura del portal.
SELECTORES_BOILERPLATE: tuple[str, ...] = (
    # No visibles.
    "script", "style", "noscript", "template", "svg", "iframe", "form", "button",
    # Navegación y pie, en las tres plantillas.
    "header", "nav", "footer", "#barra-navegacion", "#wpthemeComplementaryContent",
    # Migas de pan (se extraen antes como metadato) y barra de búsqueda de B/C.
    ".bc-breadcrumb", "div.breadcrumbs",
    # Menú de cada portlet de WebSphere ("Component Action Menu ${title} ${loading}") y
    # estado oculto del portlet (`{}`).
    ".wpthemeHiddenPlusControlHeaderParent", "#portletState",
    # Cajas de error de WCM ("Invalid configuration found").
    ".lrpError",
    # Bloque rotativo de "Contenido relacionado" (L-08).
    "section.miniatura-articulos",
    # Iconos de fuente: su texto es el nombre del icono ("arrow2-right", "check-small").
    "[class*=bc-icon]", ".lrpIcon",
    # Banner de cookies y ventanas modales (no llegan en el HTML estático, por robustez).
    "[id*=cookie i]", "[class*=cookie i]", "[role=dialog]", ".modal",
)  # fmt: skip

# Placeholders de plantillas sin resolver: ${title}, ${loading}, {{ x }}.
_PLACEHOLDER = re.compile(r"\$\{[^}\s]{1,60}\}|\{\{[^}]{1,60}\}\}")
# Encabezados de bloques de "relacionados" que pueden quedar sueltos.
_ENCABEZADOS_RELACIONADOS = re.compile(
    r"^\s*(contenido relacionado|art[ií]culos relacionados|te puede interesar)\s*$", re.I
)
# Rótulos de botones y enlaces de interfaz que no aportan contenido.
TEXTOS_INTERFAZ = frozenset(
    {
        "comparte este articulo", "comparte este artículo", "leer más", "conoce más",
        "ver más", "conocer todas", "entra ya", "buscadorfaqs",
    }
)  # fmt: skip
_SUFIJO_TITULO = re.compile(r"\s*[|\-\u2013]\s*Bancolombia\s*$", re.I)
_PALABRA = re.compile(r"\w{4,}")
_INVISIBLES = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF], None)


class CleaningStep(ABC):
    """Eslabón de la cadena de limpieza."""

    name: str = "paso"

    def __init__(self) -> None:
        self._next: CleaningStep | None = None

    def set_next(self, step: "CleaningStep") -> "CleaningStep":
        """Encadena `step` después de este paso y lo devuelve (para encadenar en línea)."""
        self._next = step
        return step

    def handle(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        """Procesa el documento y lo entrega al siguiente paso, salvo que se descarte."""
        resultado = self.process(doc)
        if isinstance(resultado, Discarded) or self._next is None:
            return resultado
        return self._next.handle(resultado)

    @abstractmethod
    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        """Transforma el documento o devuelve `Discarded` con el motivo."""

    def reset(self) -> Self:
        """Reinicia el estado entre ejecuciones (solo lo usan pasos con memoria)."""
        return self


class ParseHtmlStep(CleaningStep):
    """Interpreta el HTML crudo."""

    name = "parse_html"

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        soup = BeautifulSoup(doc.page.html, "lxml")
        if soup.body is None:
            return Discarded("html_sin_body")
        doc.soup = soup
        return doc


def _soup(doc: WorkingDocument) -> BeautifulSoup:
    """HTML interpretado del documento; exige que `ParseHtmlStep` haya corrido antes."""
    if doc.soup is None:
        raise ProcessingError("Paso de limpieza fuera de orden", detail="falta ParseHtmlStep")
    return doc.soup


def _texto(elemento: Tag) -> str:
    return " ".join(elemento.get_text(" ").split())


def _fecha_json_ld(soup: BeautifulSoup) -> str | None:
    """`datePublished` del primer bloque JSON-LD que lo tenga."""
    for bloque in soup.find_all("script", type="application/ld+json"):
        try:
            datos = json.loads(bloque.string or "")
        except json.JSONDecodeError:
            continue
        for item in datos if isinstance(datos, list) else [datos]:
            if isinstance(item, dict) and isinstance(item.get("datePublished"), str):
                return item["datePublished"].strip() or None
    return None


class ExtractMetadataStep(CleaningStep):
    """Título, idioma, fecha de publicación, migas de pan y plantilla.

    Se ejecuta antes de quitar el boilerplate porque varias fuentes (meta, JSON-LD,
    migas) están en zonas que luego se eliminan.
    """

    name = "metadatos"

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        soup = _soup(doc)
        if soup.title and (titulo := " ".join(soup.title.get_text(" ").split())):
            doc.title = _SUFIJO_TITULO.sub("", titulo) or titulo
        elif (h1 := soup.find("h1")) and (texto := _texto(h1)):
            doc.title = texto

        html = soup.find("html")
        lang = html.get("lang") if isinstance(html, Tag) else None
        doc.html_lang = str(lang).strip() or None if lang else None

        meta = soup.find("meta", attrs={"property": "article:published_time"})
        contenido = meta.get("content") if isinstance(meta, Tag) else None
        doc.published_at = str(contenido).strip() if contenido else _fecha_json_ld(soup)

        migas = soup.select_one(".bc-breadcrumb-items")
        if migas is not None:
            doc.breadcrumbs = [t for li in migas.find_all("li") if (t := _texto_sin_iconos(li))]

        doc.template = "otra"
        for selector, plantilla in SELECTORES_PLANTILLA:
            if soup.select_one(selector) is not None:
                doc.template = plantilla
                break
        return doc


def _es_texto_interfaz(texto: str | None) -> bool:
    return bool(texto) and " ".join(str(texto).split()).lower() in TEXTOS_INTERFAZ


def _texto_sin_iconos(elemento: Tag) -> str:
    """Texto del elemento sin el nombre de los iconos de fuente que contiene."""
    partes = [
        str(t)
        for t in elemento.find_all(string=True)
        if not any(
            "bc-icon" in " ".join(p.get("class") or []) for p in t.parents if isinstance(p, Tag)
        )
    ]
    return " ".join(" ".join(partes).split())


class RemoveBoilerplateStep(CleaningStep):
    """Quita navegación, pie, menús de portlet, errores de WCM, iconos, cookies y el
    bloque rotativo de contenido relacionado (L-08)."""

    name = "boilerplate"

    def __init__(self, selectors: tuple[str, ...] = SELECTORES_BOILERPLATE) -> None:
        super().__init__()
        self.selectors = selectors

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        soup = _soup(doc)
        for selector in self.selectors:
            for elemento in soup.select(selector):
                if elemento.name not in ("html", "body", "main"):
                    elemento.decompose()
        for texto in soup.find_all(string=_PLACEHOLDER):
            texto.replace_with(_PLACEHOLDER.sub("", str(texto)))
        for texto in soup.find_all(string=_ENCABEZADOS_RELACIONADOS):
            texto.extract()
        for texto in soup.find_all(string=_es_texto_interfaz):
            texto.extract()
        return doc


def word_coverage(referencia: str, candidato: str) -> float:
    """Fracción de las palabras (de 4+ letras) de `referencia` presentes en `candidato`."""
    palabras = set(_PALABRA.findall(referencia.lower()))
    if not palabras:
        return 1.0
    return len(palabras & set(_PALABRA.findall(candidato.lower()))) / len(palabras)


class ExtractMainContentStep(CleaningStep):
    """Contenido principal del contenedor de la plantilla (`main` → `#main-content` →
    `[role=main]` → `body`), ya sin boilerplate.

    Se usa trafilatura si conserva al menos `min_coverage` del vocabulario del
    contenedor; si extrae menos ("poco"), el *fallback* es el contenedor convertido a
    markdown por selector. En el HTML real de M2 trafilatura nunca agregó palabras
    ausentes del contenedor, pero en varias páginas omitió el título y secciones
    enteras (`docs/modulos/M03.md` §4).
    """

    name = "contenido_principal"

    def __init__(self, min_coverage: float) -> None:
        super().__init__()
        self.min_coverage = min_coverage

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        soup = _soup(doc)
        contenedor: Tag | None = None
        for selector, _ in SELECTORES_PLANTILLA:
            if (contenedor := soup.select_one(selector)) is not None:
                break
        contenedor = contenedor or soup.body
        if contenedor is None:
            return Discarded("sin_contenedor")
        doc.container = contenedor

        selector_md = html_to_markdown(contenedor)
        extraido = trafilatura.extract(
            f"<html><body>{contenedor}</body></html>",
            output_format="markdown",
            include_tables=True,
            include_formatting=True,
            include_links=False,
            include_images=False,
            include_comments=False,
            favor_recall=True,
            deduplicate=False,
        )
        if extraido and word_coverage(selector_md, extraido) >= self.min_coverage:
            doc.text, doc.extraction = extraido, "trafilatura"
        else:
            doc.text, doc.extraction = selector_md, "selector"
        return doc


# Bloques de al menos este largo que se repiten en la página (versiones de escritorio
# y móvil del mismo componente) se conservan una sola vez.
MIN_CHARS_BLOQUE_REPETIDO = 60


def normalize_text(texto: str) -> str:
    """Normaliza el texto extraído.

    - Unicode NFC, sin caracteres invisibles y con espacios no separables como espacios.
    - Espacios colapsados en cada línea y como máximo una línea en blanco seguida.
    - Sin bloques consecutivos idénticos, ni bloques largos ya vistos en el documento.
    """
    texto = unicodedata.normalize("NFC", texto).translate(_INVISIBLES)
    texto = texto.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lineas = [" ".join(linea.split()) for linea in texto.split("\n")]
    bloques: list[str] = []
    vistos: set[str] = set()
    actual: list[str] = []
    for linea in [*lineas, ""]:
        if linea:
            actual.append(linea)
            continue
        if not actual:
            continue
        bloque = "\n".join(actual)
        actual = []
        if bloques and bloques[-1] == bloque:
            continue
        if len(bloque) >= MIN_CHARS_BLOQUE_REPETIDO:
            if bloque in vistos:
                continue
            vistos.add(bloque)
        bloques.append(bloque)
    return "\n\n".join(bloques)


class NormalizeTextStep(CleaningStep):
    """Normaliza Unicode y espacios del texto extraído."""

    name = "normalizar"

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        doc.text = normalize_text(doc.text)
        return doc


# Palabras funcionales frecuentes y exclusivas de cada idioma (se evitan las ambiguas
# como "a", "no" o "me").
STOPWORDS: dict[str, frozenset[str]] = {
    "es": frozenset(
        [
            "de", "la", "que", "el", "en", "y", "los", "del", "se", "las", "por", "un", "para",
            "con", "una", "su", "al", "lo", "como", "más", "pero", "sus", "ya", "este", "esta",
            "entre", "cuando", "muy", "sin", "sobre", "también", "hasta", "hay", "donde",
            "desde", "todo", "nos", "tu", "tus", "puedes", "es", "son",
        ]
    ),
    "en": frozenset(
        [
            "the", "and", "of", "to", "in", "is", "that", "for", "it", "with", "as", "was", "on",
            "are", "be", "this", "by", "you", "your", "from", "at", "or", "an", "have", "not",
            "can", "will", "our", "we", "they",
        ]
    ),
}  # fmt: skip
# Mínimo de palabras funcionales y ventaja (veces) del idioma ganador sobre el otro.
MIN_STOPWORDS_IDIOMA = 5
VENTAJA_IDIOMA = 2.0
_TOKEN = re.compile(r"[a-záéíóúüñ]+")


def detect_language(texto: str) -> str | None:
    """Idioma del texto (`es`/`en`) por conteo de palabras funcionales.

    Devuelve `None` si no hay señal clara: menos de `MIN_STOPWORDS_IDIOMA` coincidencias
    o ningún idioma supera al otro por `VENTAJA_IDIOMA` veces.
    """
    conteo = dict.fromkeys(STOPWORDS, 0)
    for token in _TOKEN.findall(texto.lower()):
        for idioma, palabras in STOPWORDS.items():
            if token in palabras:
                conteo[idioma] += 1
    (primero, n1), (_, n2) = sorted(conteo.items(), key=lambda kv: -kv[1])
    if n1 + n2 < MIN_STOPWORDS_IDIOMA or n1 < VENTAJA_IDIOMA * n2:
        return None
    return primero


def primary_language(etiqueta: str | None) -> str | None:
    """Subetiqueta principal de una etiqueta BCP 47 (`es-CO` → `es`)."""
    return etiqueta.split("-")[0].strip().lower() or None if etiqueta else None


class DetectLanguageStep(CleaningStep):
    """Idioma real del contenido. Necesario porque la plantilla C (WebSphere) declara
    `<html lang="en">` en páginas escritas en español; `html_lang` conserva el valor
    declarado para trazabilidad."""

    name = "idioma"

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        doc.lang = detect_language(doc.text) or primary_language(doc.html_lang)
        return doc


class MinLengthFilterStep(CleaningStep):
    """Descarta documentos con menos de `min_chars` caracteres de texto."""

    name = "longitud_minima"

    def __init__(self, min_chars: int) -> None:
        super().__init__()
        self.min_chars = min_chars

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        if len(doc.text) < self.min_chars:
            return Discarded("texto_corto", f"{len(doc.text)} < {self.min_chars} caracteres")
        return doc


def text_hash(texto: str) -> str:
    """SHA-256 del texto limpio."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class DeduplicateStep(CleaningStep):
    """Descarta documentos cuyo texto limpio ya apareció (duplicados y soft-404)."""

    name = "deduplicar"

    def __init__(self) -> None:
        super().__init__()
        self._vistos: dict[str, str] = {}

    def reset(self) -> Self:
        self._vistos = {}
        return self

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        doc.content_hash = text_hash(doc.text)
        if previo := self._vistos.get(doc.content_hash):
            return Discarded("texto_duplicado", f"mismo texto que {previo}")
        self._vistos[doc.content_hash] = doc.page.url
        return doc
