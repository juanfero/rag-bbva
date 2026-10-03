"""Post-procesamiento de citas [n] → URL (M7).

- Las citas que no corresponden a ningún fragmento del contexto se quitan del texto.
- Varias citas a la misma página se renumeran a una sola fuente (y `[1][1]` queda `[1]`).
- La lista de fuentes contiene solo las páginas realmente citadas, en orden de aparición.
"""

import re
from collections.abc import Sequence

from pydantic import BaseModel

from rag_bbva.retrieval.models import Candidate

# [1], [2, 3], [2,3] o [2 ,3]
_CITA = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


class Source(BaseModel):
    """Fuente citada en la respuesta."""

    number: int
    url: str
    title: str | None
    heading_path: str


def process_citations(text: str, results: Sequence[Candidate]) -> tuple[str, list[Source]]:
    """Reescribe las citas del texto y devuelve las fuentes citadas."""
    numero_por_url: dict[str, int] = {}
    fuentes: list[Source] = []

    def fuente_de(indice: int) -> int | None:
        if not 1 <= indice <= len(results):
            return None
        candidato = results[indice - 1]
        if candidato.url not in numero_por_url:
            numero_por_url[candidato.url] = len(fuentes) + 1
            fuentes.append(
                Source(
                    number=numero_por_url[candidato.url],
                    url=candidato.url,
                    title=candidato.title,
                    heading_path=candidato.heading_path,
                )
            )
        return numero_por_url[candidato.url]

    def reemplazar(m: re.Match[str]) -> str:
        numeros: list[int] = []
        for parte in m.group(1).split(","):
            n = fuente_de(int(parte))
            if n is not None and n not in numeros:
                numeros.append(n)
        return "".join(f"[{n}]" for n in numeros)

    texto = _CITA.sub(reemplazar, text)
    texto = re.sub(r"(\[\d+\])(?:\1)+", r"\1", texto)  # [1][1] → [1]
    texto = re.sub(r"[ \t]+([.,;:])", r"\1", texto)  # espacio que quedó antes de puntuación
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    return texto.strip(), fuentes
