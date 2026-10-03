"""Funciones de presentación de la interfaz (M10), sin Streamlit: se prueban solas."""

import re
from collections.abc import Sequence
from typing import Any

from rag_bbva.exceptions import ApiClientError

AVISO = "Prototipo de prueba técnica. No es un canal oficial de Bancolombia."
SIN_INFO_TITULO = "Sin información suficiente en el sitio de Bancolombia"

_CITA = re.compile(r"\[(\d+)\]")


def escape_markdown(texto: str) -> str:
    """Escapa `$`: Streamlit interpreta `$…$` como LaTeX y las respuestas traen montos
    (p. ej. "$151.994 + IVA")."""
    return texto.replace("$", r"\$")


def link_citations(texto: str, sources: Sequence[dict[str, Any]]) -> str:
    """Convierte cada cita [n] en un enlace a su fuente; deja las citas sin fuente."""
    urls = {int(s["n"]): str(s["url"]) for s in sources if "n" in s and "url" in s}

    def enlace(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return f"[\\[{n}\\]]({urls[n]})" if n in urls else m.group(0)

    return _CITA.sub(enlace, escape_markdown(texto))


def source_lines(sources: Sequence[dict[str, Any]]) -> list[str]:
    """Líneas markdown para el desplegable de fuentes: número, título y URL."""
    lineas = []
    for s in sources:
        titulo = escape_markdown(str(s.get("title") or s["url"]))
        lineas.append(f"**[{s['n']}]** [{titulo}]({s['url']})  \n`{s['url']}`")
    return lineas


def shorten(texto: str, maximo: int = 80) -> str:
    """Texto en una línea y recortado, para citar la pregunta en un mensaje de error."""
    linea = " ".join(texto.split())
    return linea if len(linea) <= maximo else linea[: maximo - 1] + "…"


def friendly_error(exc: ApiClientError) -> str:
    """Mensaje para el usuario según el código HTTP de la API."""
    if exc.status == 404:
        return (
            "No encontré esa conversación (puede que el ID esté mal copiado). "
            "Inicie una conversación nueva o elija otra de la lista."
        )
    if exc.status == 422:
        detalle = (exc.detail or "").removeprefix("question: ")
        return f"La pregunta no es válida: {detalle or exc.message}"
    if exc.status == 503:
        return f"El asistente no está disponible en este momento. {exc.message}"
    return exc.message


def health_lines(health: Any) -> list[tuple[str, bool, str]]:
    """`(componente, ok, detalle)` para la barra lateral, a partir del /health."""
    llm = health.llm
    modelo = llm.model or "?"
    if llm.fallback_model:
        modelo += f" (respaldo: {llm.fallback_model})"
    return [
        ("Búsqueda (Qdrant)", health.qdrant.status == "ok", health.qdrant.detail or ""),
        ("Historial (SQLite)", health.sqlite.status == "ok", health.sqlite.detail or ""),
        ("LLM", llm.status == "ok", llm.detail or modelo),
    ]


def detail_lines(detalle: dict[str, Any]) -> list[str]:
    """Líneas del "modo detalle": reformulación, tiempos, tokens y modelo."""
    lineas = []
    if detalle.get("rewritten_query"):
        lineas.append(f"**Pregunta reformulada:** {escape_markdown(detalle['rewritten_query'])}")
    if detalle.get("gray_zone"):
        lineas.append("**Zona gris del umbral:** el LLM decidió si el contexto alcanzaba")
    if t := detalle.get("timings"):
        lineas.append(
            "**Tiempos (ms):** "
            + " · ".join(f"{k} {v:,.0f}" for k, v in t.items() if v is not None)
        )
    if tk := detalle.get("tokens"):
        lineas.append(f"**Tokens:** {tk['prompt']} entrada + {tk['completion']} salida")
    if detalle.get("top_score") is not None:
        lineas.append(f"**Score del reranker (#1):** {detalle['top_score']:.2f}")
    lineas.append(f"**Modelo:** {detalle.get('model') or 'no se llamó al LLM'}")
    return lineas
