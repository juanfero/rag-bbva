"""Genera el snapshot versionado que usa el arranque con Docker (M12).

`docker compose up` no scrapea el sitio: indexa este snapshot. Deja en `snapshot/`:
- `documents.jsonl`: copia de los documentos limpios (`CLEAN_DATA_DIR`).
- `embeddings/<modelo>.npz`: la caché de embeddings **podada** a los chunks actuales
  (mismo formato que `EMBEDDINGS_CACHE_DIR`), para no re-embeber en CPU al arrancar.
- `MANIFEST.json`: fecha, conteos, modelo, estrategia de chunking y SHA-256 de cada archivo.

Uso (después de `clean`, `chunk` e `ingest`, con la caché llena):
    python scripts/make_snapshot.py
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from rag_bbva.config import get_settings
from rag_bbva.indexing.embedding_cache import EmbeddingCache, text_hash
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.indexing.pipeline import read_documents

DESTINO = Path("snapshot")


def _sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def main() -> None:
    ajustes = get_settings()
    fabrica = ComponentFactory(ajustes)
    documentos = read_documents(ajustes.clean_data_dir)
    chunker = fabrica.create_chunker()
    chunks = [c for d in documentos for c in chunker.chunk(d)]
    hashes = sorted({text_hash(c.embedding_text) for c in chunks})

    origen = EmbeddingCache(ajustes.embeddings_cache_dir, ajustes.embedding_model)
    faltan = [h for h in hashes if origen.get(h) is None]
    if faltan:
        raise SystemExit(f"Faltan {len(faltan)} vectores en la caché: ejecute antes `ingest`.")

    DESTINO.mkdir(exist_ok=True)
    shutil.copyfile(ajustes.clean_data_dir / "documents.jsonl", DESTINO / "documents.jsonl")
    podada = EmbeddingCache(DESTINO / "embeddings", ajustes.embedding_model)
    if podada.path.exists():
        podada.path.unlink()
    for h in hashes:
        vector = origen.get(h)
        assert vector is not None
        podada.put(h, vector)
    podada.save()

    archivos = [DESTINO / "documents.jsonl", podada.path]
    manifiesto = {
        "generado": datetime.now(UTC).isoformat(timespec="seconds"),
        "fuente": "https://www.bancolombia.com/ (contenido público; crawl de M3, limpieza de M10)",
        "documentos": len(documentos),
        "chunks": len(chunks),
        "vectores_distintos": len(hashes),  # chunks con el mismo texto comparten vector
        "chunking": {
            "estrategia": chunker.name,
            "chunk_size": ajustes.chunk_size,
            "chunk_overlap": ajustes.chunk_overlap,
        },
        "modelo_embeddings": ajustes.embedding_model,
        "archivos": {
            str(a.relative_to(DESTINO)): {"bytes": a.stat().st_size, "sha256": _sha256(a)}
            for a in archivos
        },
    }
    (DESTINO / "MANIFEST.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    print(json.dumps(manifiesto, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
