"""Pruebas de `QdrantVectorStore` con `QdrantClient(":memory:")` (M5, sin red)."""

import uuid

import httpx
import numpy as np
import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from rag_bbva.exceptions import IndexingError
from rag_bbva.indexing.vector_store import QdrantVectorStore, VectorPoint, point_id


def _vec(*valores: float) -> np.ndarray:
    v = np.array(valores, dtype=np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def store() -> QdrantVectorStore:
    almacen = QdrantVectorStore(QdrantClient(":memory:"), "prueba", batch_size=2)
    almacen.ensure_collection(4)
    return almacen


def _puntos() -> list[VectorPoint]:
    datos = [
        ("tarjetas", (1, 0, 0, 0), "personas", "h1"),
        ("cdt", (0, 1, 0, 0), "personas", "h2"),
        ("leasing", (0.9, 0.1, 0, 0), "negocios", "h3"),
        ("faq", (0, 0, 1, 0), "centro-de-ayuda", "h4"),
        ("otra", (0, 0, 0, 1), "pagos", "h5"),
    ]
    return [
        VectorPoint(point_id(n), _vec(*v), {"section": s, "text_hash": h}) for n, v, s, h in datos
    ]


def test_point_id_determinista_y_uuid() -> None:
    assert point_id("abc") == point_id("abc")
    assert point_id("abc") != point_id("abd")
    assert uuid.UUID(point_id("abc")).version == 5


def test_upsert_por_lotes_count_y_busqueda_top1(store: QdrantVectorStore) -> None:
    store.upsert(_puntos())  # 5 puntos en lotes de 2

    resultado = store.search(_vec(1, 0.05, 0, 0), top_k=1)

    assert store.count() == 5
    assert len(resultado) == 1
    assert resultado[0].id == point_id("tarjetas")
    assert resultado[0].payload["section"] == "personas"
    assert resultado[0].score == pytest.approx(0.9988, abs=1e-3)


def test_filtro_por_seccion(store: QdrantVectorStore) -> None:
    store.upsert(_puntos())

    resultado = store.search(_vec(1, 0.05, 0, 0), top_k=3, section="negocios")

    assert [h.id for h in resultado] == [point_id("leasing")]


def test_upsert_mismo_id_no_duplica(store: QdrantVectorStore) -> None:
    store.upsert(_puntos())
    store.upsert(_puntos()[:2])

    assert store.count() == 5


def test_borrado_por_ids_y_hashes(store: QdrantVectorStore) -> None:
    store.upsert(_puntos())

    store.delete([point_id("cdt"), point_id("faq"), point_id("otra")])

    assert store.count() == 2
    assert store.stored_hashes() == {point_id("tarjetas"): "h1", point_id("leasing"): "h3"}


def test_stored_hashes_pagina_con_scroll() -> None:
    almacen = QdrantVectorStore(QdrantClient(":memory:"), "grande", batch_size=500)
    almacen.ensure_collection(4)
    almacen.upsert(
        [
            VectorPoint(point_id(str(i)), _vec(1, i, 0, 1), {"text_hash": f"h{i}"})
            for i in range(1500)
        ]
    )

    hashes = almacen.stored_hashes()

    assert len(hashes) == 1500
    assert hashes[point_id("1499")] == "h1499"


def test_recreate_vacia_la_coleccion(store: QdrantVectorStore) -> None:
    store.upsert(_puntos())

    store.ensure_collection(4, recreate=True)

    assert store.count() == 0
    store.ensure_collection(4)  # existente: no la borra
    store.upsert(_puntos()[:1])
    store.ensure_collection(4)
    assert store.count() == 1


def test_from_url_memoria() -> None:
    almacen = QdrantVectorStore.from_url(":memory:", "c")
    almacen.ensure_collection(4)

    assert almacen.count() == 0


class _ClienteCaido:
    """Doble de `QdrantClient` que falla como un servidor inalcanzable."""

    def __getattr__(self, nombre: str) -> object:
        def fallar(*_a: object, **_k: object) -> None:
            raise ResponseHandlingException(httpx.ConnectError("Connection refused"))

        return fallar


def test_error_de_conexion_es_indexing_error_con_mensaje_claro() -> None:
    almacen = QdrantVectorStore(_ClienteCaido(), "c", url="http://localhost:6333")  # type: ignore[arg-type]

    with pytest.raises(IndexingError) as error:
        almacen.count()

    assert "No se pudo conectar con Qdrant en http://localhost:6333" in str(error.value)
    assert "docker compose up -d qdrant" in str(error.value)
    with pytest.raises(IndexingError):
        almacen.ensure_collection(4)


def test_respuesta_inesperada_es_indexing_error() -> None:
    class _Rechazo:
        def count(self, *_a: object, **_k: object) -> None:
            raise UnexpectedResponse(400, "Bad Request", b"{}", httpx.Headers())

    with pytest.raises(IndexingError, match="rechazó"):
        QdrantVectorStore(_Rechazo(), "c").count()  # type: ignore[arg-type]
