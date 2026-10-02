"""Modelos del chunking (M4): un fragmento de documento listo para embeber."""

from pydantic import BaseModel


class Chunk(BaseModel):
    """Fragmento de un documento limpio; una línea de `data/chunks/chunks.jsonl`."""

    chunk_id: str  # SHA-1 de (doc_id, estrategia, posición, texto): estable entre corridas
    doc_id: str
    url: str
    title: str | None
    section: str
    heading_path: str  # "Título > Sección > Subsección" según los títulos markdown
    lang: str | None
    position: int  # orden del chunk dentro del documento (desde 0)
    n_chars: int  # longitud de `text`
    chunker: str  # estrategia que lo generó
    text: str  # contenido del fragmento (lo que se cita)
    embedding_text: str  # encabezado de contexto + texto: lo que se embebe
