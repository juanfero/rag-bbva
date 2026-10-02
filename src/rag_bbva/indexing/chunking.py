"""Estrategias de chunking (patrón Strategy).

- `HeadingAwareChunker`: respeta la estructura markdown de la limpieza (M3). Agrupa
  secciones consecutivas mientras quepan en `chunk_size` y parte por tamaño solo las
  secciones que no caben, con solapamiento y sin cortar palabras.
- `FixedSizeChunker`: línea base; parte el texto completo por tamaño, ignorando títulos.

Ambas producen `Chunk` con un encabezado de contexto (título, sección y ruta de
títulos) dentro de `embedding_text`, para que el vector conserve de qué página y
apartado viene un fragmento corto.
"""

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from rag_bbva.exceptions import IndexingError
from rag_bbva.indexing.models import Chunk
from rag_bbva.processing.models import CleanDocument

_TITULO_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_PALABRA = re.compile(r"\w")
SEPARADOR_RUTA = " > "
# Un corte por párrafo u oración solo se acepta si deja el chunk al menos así de lleno.
_MIN_LLENADO_CORTE = 0.5
# Un grupo de secciones más corto que esta fracción de `chunk_size` que precede a una
# sección que no cabe (p. ej. un título suelto) se antepone a ella en vez de quedar
# como chunk aparte.
_MAX_FRACCION_PREAMBULO = 0.25


def _mejor_corte(ventana: str) -> int:
    """Posición de corte dentro de `ventana`: fin de párrafo, de oración o de palabra.

    Prefiere el último salto de párrafo y luego el último fin de oración, siempre que
    dejen el chunk al menos a medio llenar; si no, el último espacio. Solo corta dentro
    de una palabra si la ventana entera es una sola palabra.
    """
    minimo = int(len(ventana) * _MIN_LLENADO_CORTE)
    parrafo = ventana.rfind("\n\n")
    if parrafo >= minimo:
        return parrafo
    oracion = max(ventana.rfind(". "), ventana.rfind(".\n"), ventana.rfind("? "))
    if oracion >= minimo:
        return oracion + 1
    espacio = max(ventana.rfind(" "), ventana.rfind("\n"))
    return espacio if espacio > 0 else len(ventana)


def split_text(texto: str, size: int, overlap: int) -> list[str]:
    """Parte `texto` en fragmentos de hasta `size` caracteres, sin cortar palabras.

    Cada fragmento (salvo el primero) empieza con hasta `overlap` caracteres del final
    del anterior, alineados al comienzo de una palabra.
    """
    if overlap >= size:
        raise IndexingError(
            "El solapamiento debe ser menor que el tamaño del chunk",
            detail=f"overlap={overlap}, size={size}",
        )
    piezas: list[str] = []
    inicio, total = 0, len(texto)
    while inicio < total:
        fin = min(inicio + size, total)
        if fin < total:
            fin = inicio + _mejor_corte(texto[inicio:fin])
        pieza = texto[inicio:fin].strip()
        if pieza:
            piezas.append(pieza)
        if fin >= total:
            break
        siguiente = fin - overlap
        if siguiente <= inicio:
            siguiente = fin
        else:
            # Avanza hasta el comienzo de una palabra para no arrancar a mitad.
            while siguiente < fin and not texto[siguiente - 1].isspace():
                siguiente += 1
        inicio = siguiente
    return piezas


@dataclass(frozen=True)
class _Seccion:
    ruta: tuple[str, ...]
    texto: str


def split_sections(texto: str) -> list[_Seccion]:
    """Divide el markdown en secciones por título; cada una conserva su ruta de títulos
    (pila de títulos de nivel superior) y su propio título como primera línea."""
    secciones: list[_Seccion] = []
    pila: list[tuple[int, str]] = []
    lineas: list[str] = []

    def cerrar() -> None:
        contenido = "\n".join(lineas).strip()
        if contenido:
            secciones.append(_Seccion(tuple(t for _, t in pila), contenido))
        lineas.clear()

    for linea in texto.split("\n"):
        if m := _TITULO_MD.match(linea):
            cerrar()
            nivel = len(m.group(1))
            pila = [(n, t) for n, t in pila if n < nivel]
            pila.append((nivel, m.group(2).strip()))
        lineas.append(linea)
    cerrar()
    return secciones


def _tiene_contenido(texto: str) -> bool:
    """El fragmento tiene texto propio: no está vacío ni es solo una lista de títulos
    (p. ej. "### Chat" al final de una página)."""
    return any(
        _PALABRA.search(linea) and not _TITULO_MD.match(linea) for linea in texto.split("\n")
    )


def _ruta_grupo(secciones: list[_Seccion]) -> tuple[str, ...]:
    """Ruta común de un grupo, sin contar las secciones que son solo un título (p. ej.
    las letras vacías "## A", "## B" del glosario)."""
    con_texto = [s for s in secciones if _tiene_contenido(s.texto)] or secciones
    return _prefijo_comun([s.ruta for s in con_texto])


def _prefijo_comun(rutas: list[tuple[str, ...]]) -> tuple[str, ...]:
    comun: list[str] = []
    for niveles in zip(*rutas, strict=False):
        if len(set(niveles)) != 1:
            break
        comun.append(niveles[0])
    return tuple(comun)


class ChunkingStrategy(ABC):
    """Interfaz de las estrategias de chunking."""

    name: str

    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        if chunk_overlap >= chunk_size:
            raise IndexingError(
                "CHUNK_OVERLAP debe ser menor que CHUNK_SIZE",
                detail=f"{chunk_overlap} >= {chunk_size}",
            )
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @abstractmethod
    def _fragmentos(self, doc: CleanDocument) -> list[tuple[str, str]]:
        """Pares (ruta de títulos, texto) del documento, en orden."""

    def chunk(self, doc: CleanDocument) -> list[Chunk]:
        """Trocea el documento y arma los `Chunk` con metadatos y contexto."""
        chunks: list[Chunk] = []
        for ruta, texto in self._fragmentos(doc):
            if not _tiene_contenido(texto):
                continue
            posicion = len(chunks)
            clave = f"{doc.doc_id}\n{self.name}\n{posicion}\n{texto}"
            chunks.append(
                Chunk(
                    chunk_id=hashlib.sha1(clave.encode("utf-8")).hexdigest(),
                    doc_id=doc.doc_id,
                    url=doc.url,
                    title=doc.title,
                    section=doc.section,
                    heading_path=ruta,
                    lang=doc.lang,
                    position=posicion,
                    n_chars=len(texto),
                    chunker=self.name,
                    text=texto,
                    embedding_text=context_header(doc, ruta) + texto,
                )
            )
        return chunks


def context_header(doc: CleanDocument, heading_path: str) -> str:
    """Encabezado que se antepone al texto a embeber: título y sección del sitio, y la
    ruta de títulos si aporta algo más que el título."""
    titulo = doc.title or doc.url
    lineas = [f"{titulo} | {doc.section}"]
    if heading_path and heading_path != titulo:
        lineas.append(heading_path)
    return "\n".join(lineas) + "\n\n"


class HeadingAwareChunker(ChunkingStrategy):
    """Chunks por secciones markdown; parte por tamaño solo lo que no cabe."""

    name = "heading_aware"

    def _ruta(self, doc: CleanDocument, ruta: tuple[str, ...], texto_doc: str) -> str:
        """Ruta legible: si el documento no abre con un título `#`, se antepone su título."""
        abre_con_h1 = texto_doc.lstrip().startswith("# ")
        partes = list(ruta) if abre_con_h1 or not doc.title else [doc.title, *ruta]
        return SEPARADOR_RUTA.join(partes)

    def _fragmentos(self, doc: CleanDocument) -> list[tuple[str, str]]:
        resultado: list[tuple[str, str]] = []
        grupo: list[_Seccion] = []

        def cerrar_grupo() -> list[_Seccion]:
            """Emite el grupo y devuelve sus títulos vacíos finales, que pasan al siguiente."""
            arrastre: list[_Seccion] = []
            while grupo and not _tiene_contenido(grupo[-1].texto):
                arrastre.insert(0, grupo.pop())
            if grupo:
                texto = "\n\n".join(s.texto for s in grupo)
                resultado.append((self._ruta(doc, _ruta_grupo(grupo), doc.text), texto))
                grupo.clear()
            return arrastre

        for seccion in split_sections(doc.text):
            if len(seccion.texto) > self.chunk_size:
                largo_grupo = sum(len(s.texto) + 2 for s in grupo)
                if grupo and largo_grupo <= _MAX_FRACCION_PREAMBULO * self.chunk_size:
                    texto = "\n\n".join([*(s.texto for s in grupo), seccion.texto])
                    seccion = _Seccion(_ruta_grupo([*grupo, seccion]), texto)
                    grupo.clear()
                else:
                    arrastre = cerrar_grupo()
                    if arrastre:
                        texto = "\n\n".join([*(s.texto for s in arrastre), seccion.texto])
                        seccion = _Seccion(seccion.ruta, texto)
                ruta = self._ruta(doc, seccion.ruta, doc.text)
                resultado += [
                    (ruta, pieza)
                    for pieza in split_text(seccion.texto, self.chunk_size, self.chunk_overlap)
                ]
                continue
            largo_grupo = sum(len(s.texto) + 2 for s in grupo)
            if grupo and largo_grupo + len(seccion.texto) > self.chunk_size:
                arrastre = cerrar_grupo()
                # Los títulos vacíos pasan al grupo siguiente solo si caben con él; si no,
                # se omiten del texto (su ruta ya está en el heading_path de la sección).
                if sum(len(s.texto) + 2 for s in arrastre) + len(seccion.texto) <= self.chunk_size:
                    grupo[:] = arrastre
            grupo.append(seccion)
        cerrar_grupo()
        return resultado


class FixedSizeChunker(ChunkingStrategy):
    """Línea base: parte el texto completo por tamaño, sin mirar los títulos."""

    name = "fixed_size"

    def _fragmentos(self, doc: CleanDocument) -> list[tuple[str, str]]:
        ruta = doc.title or ""
        return [
            (ruta, pieza) for pieza in split_text(doc.text, self.chunk_size, self.chunk_overlap)
        ]
