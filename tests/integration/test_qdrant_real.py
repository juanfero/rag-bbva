"""Prueba contra el Qdrant real del compose (`docker compose up -d qdrant`).

Marcada `integration`: se salta si Qdrant no responde en QDRANT_URL.
"""

import uuid

import httpx
import numpy as np
import pytest

from rag_bbva.config import get_settings
from rag_bbva.indexing.vector_store import QdrantVectorStore, VectorPoint, point_id

pytestmark = pytest.mark.integration


@pytest.fixture
def store_real() -> QdrantVectorStore:
    url = get_settings().qdrant_url
    try:
        httpx.get(f"{url}/readyz", timeout=2).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"Qdrant no responde en {url}; levántelo con `docker compose up -d qdrant`")
    coleccion = f"prueba_{uuid.uuid4().hex[:8]}"
    almacen = QdrantVectorStore.from_url(url, coleccion, batch_size=2)
    yield almacen
    almacen.client.delete_collection(coleccion)


def test_ciclo_completo_contra_qdrant_real(store_real: QdrantVectorStore) -> None:
    """Colección coseno, índice de payload, upsert, búsqueda con filtro, borrado."""
    store_real.ensure_collection(4)
    vectores = np.eye(4, dtype=np.float32)
    secciones = ["personas", "negocios", "personas", "pagos"]
    store_real.upsert(
        [
            VectorPoint(point_id(f"c{i}"), vectores[i], {"section": s, "text_hash": f"h{i}"})
            for i, s in enumerate(secciones)
        ]
    )

    info = store_real.client.get_collection(store_real.collection)
    assert info.config.params.vectors.distance.value == "Cosine"  # type: ignore[union-attr]
    assert "section" in (info.payload_schema or {})
    assert store_real.count() == 4
    assert store_real.search(vectores[2], 1)[0].id == point_id("c2")
    assert [h.id for h in store_real.search(vectores[1], 4, section="negocios")] == [point_id("c1")]

    store_real.delete([point_id("c0"), point_id("c3")])
    assert store_real.count() == 2
