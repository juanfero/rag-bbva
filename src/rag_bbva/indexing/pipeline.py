"""Chunking de los documentos limpios: `data/clean/` → `data/chunks/`."""

import json
import logging
import os
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from rag_bbva.exceptions import IndexingError
from rag_bbva.indexing.chunking import ChunkingStrategy
from rag_bbva.indexing.embedding import Embedder
from rag_bbva.indexing.models import Chunk
from rag_bbva.processing.models import CleanDocument

logger = logging.getLogger(__name__)
MAX_EJEMPLOS = 10


def read_documents(clean_dir: Path) -> list[CleanDocument]:
    """Lee `documents.jsonl` de la limpieza (M3)."""
    ruta = clean_dir / "documents.jsonl"
    if not ruta.exists():
        raise IndexingError("No hay documentos limpios; ejecute antes `clean`", detail=str(ruta))
    documentos = []
    for numero, linea in enumerate(ruta.read_text("utf-8").splitlines(), 1):
        if not linea.strip():
            continue
        try:
            documentos.append(CleanDocument.model_validate_json(linea))
        except ValidationError as exc:
            raise IndexingError("Documento limpio inválido", detail=f"línea {numero}") from exc
    return documentos


class Stats(BaseModel):
    """Mínimo, mediana, percentil 95 y máximo."""

    min: int
    p50: int
    p95: int
    max: int


def _stats(valores: Sequence[int]) -> Stats | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    p95 = ordenados[max(0, min(len(ordenados) - 1, round(0.95 * len(ordenados) + 0.5) - 1))]
    return Stats(
        min=ordenados[0], p50=int(statistics.median_low(ordenados)), p95=p95, max=ordenados[-1]
    )


class ShortChunk(BaseModel):
    """Chunk por debajo de `CHUNK_MIN_CHARS`."""

    chunk_id: str
    url: str
    heading_path: str
    n_chars: int
    text: str


class ChunkReport(BaseModel):
    """Resumen del chunking (`data/chunks/chunk_report.json`)."""

    chunker: str
    chunk_size: int
    chunk_overlap: int
    documents: int
    total: int
    n_chars: Stats | None
    chunks_per_document: Stats | None
    documents_with_most_chunks: dict[str, int]
    by_section: dict[str, int]
    short_threshold: int
    short_chunks: int
    short_examples: list[ShortChunk]
    max_tokens: int
    tokens: Stats | None
    over_max_tokens: int
    over_max_tokens_ids: list[str]


@dataclass
class ChunkingResult:
    """Chunks y reporte de una ejecución."""

    chunks: list[Chunk]
    report: ChunkReport


def _ordenado(conteo: Counter[str]) -> dict[str, int]:
    return dict(sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0])))


def chunk_documents(
    documents: Sequence[CleanDocument],
    chunker: ChunkingStrategy,
    token_counter: Embedder,
    min_chars: int,
) -> ChunkingResult:
    """Trocea los documentos y verifica los tokens de cada chunk con `token_counter`.

    Se cuenta sobre `embedding_text` (encabezado de contexto + texto, con el prefijo
    `passage: `), que es exactamente lo que recibe el modelo.
    """
    chunks: list[Chunk] = []
    por_documento: Counter[str] = Counter()
    for doc in documents:
        piezas = chunker.chunk(doc)
        chunks += piezas
        por_documento[doc.url] = len(piezas)
    tokens = {c.chunk_id: token_counter.count_tokens(c.embedding_text) for c in chunks}
    maximo = token_counter.max_tokens
    excedidos = [cid for cid, n in tokens.items() if n > maximo]
    cortos = [c for c in chunks if c.n_chars < min_chars]
    reporte = ChunkReport(
        chunker=chunker.name,
        chunk_size=chunker.chunk_size,
        chunk_overlap=chunker.chunk_overlap,
        documents=len(documents),
        total=len(chunks),
        n_chars=_stats([c.n_chars for c in chunks]),
        chunks_per_document=_stats(list(por_documento.values())),
        documents_with_most_chunks=dict(por_documento.most_common(5)),
        by_section=_ordenado(Counter(c.section for c in chunks)),
        short_threshold=min_chars,
        short_chunks=len(cortos),
        short_examples=[
            ShortChunk(
                chunk_id=c.chunk_id,
                url=c.url,
                heading_path=c.heading_path,
                n_chars=c.n_chars,
                text=c.text,
            )
            for c in cortos[:MAX_EJEMPLOS]
        ],
        max_tokens=maximo,
        tokens=_stats(list(tokens.values())),
        over_max_tokens=len(excedidos),
        over_max_tokens_ids=excedidos[:MAX_EJEMPLOS],
    )
    logger.info("Chunking terminado", extra={"documentos": len(documents), "chunks": len(chunks)})
    return ChunkingResult(chunks, reporte)


def _escribir_atomico(destino: Path, texto: str) -> None:
    temporal = destino.with_name(f".{destino.name}.tmp")
    temporal.write_text(texto, encoding="utf-8")
    os.replace(temporal, destino)


def write_chunks(result: ChunkingResult, chunks_dir: Path) -> tuple[Path, Path]:
    """Escribe `chunks.jsonl` y `chunk_report.json` de forma atómica."""
    try:
        chunks_dir.mkdir(parents=True, exist_ok=True)
        ruta_chunks = chunks_dir / "chunks.jsonl"
        ruta_reporte = chunks_dir / "chunk_report.json"
        _escribir_atomico(
            ruta_chunks,
            "".join(
                json.dumps(c.model_dump(mode="json"), ensure_ascii=False) + "\n"
                for c in result.chunks
            ),
        )
        _escribir_atomico(
            ruta_reporte,
            json.dumps(result.report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        )
    except OSError as exc:
        raise IndexingError("No se pudieron escribir los chunks", detail=str(exc)) from exc
    return ruta_chunks, ruta_reporte


def read_chunks(chunks_dir: Path) -> list[Chunk]:
    """Lee `chunks.jsonl`."""
    ruta = chunks_dir / "chunks.jsonl"
    if not ruta.exists():
        raise IndexingError("No hay chunks; ejecute antes `chunk`", detail=str(ruta))
    return [Chunk.model_validate_json(x) for x in ruta.read_text("utf-8").splitlines() if x]
