"""Pruebas del almacenamiento crudo y el manifest (M2, sin red)."""

import hashlib
import json
from pathlib import Path

import pytest

from rag_bbva.scraping.storage import ManifestEntry, RawStorage, sha256, text_fingerprint

URL = "https://www.banco.test/personas"


def _entrada(url: str = URL, **campos: object) -> ManifestEntry:
    base: dict[str, object] = {
        "url": url,
        "outcome": "guardada",
        "fetched_at": "2026-10-01T00:00:00+00:00",
        "depth": 0,
        "source": "sitemap",
    }
    base.update(campos)
    return ManifestEntry.model_validate(base)


def test_save_page_escribe_en_pages_con_sha1(tmp_path: Path) -> None:
    """El HTML se guarda en pages/<sha1(url)>.html con su hash SHA-256."""
    storage = RawStorage(tmp_path)

    relativa, hash_, escrito = storage.save_page(URL, b"<html>hola</html>")

    assert relativa == f"pages/{hashlib.sha1(URL.encode()).hexdigest()}.html"
    assert (tmp_path / relativa).read_bytes() == b"<html>hola</html>"
    assert hash_ == sha256(b"<html>hola</html>")
    assert escrito is True


def test_save_page_incremental_no_reescribe_si_no_cambia(tmp_path: Path) -> None:
    """Mismo contenido → no se toca el archivo (mtime intacto)."""
    storage = RawStorage(tmp_path)
    relativa, _, _ = storage.save_page(URL, b"<html>v1</html>")
    archivo = tmp_path / relativa
    mtime = archivo.stat().st_mtime_ns

    _, _, escrito = storage.save_page(URL, b"<html>v1</html>")

    assert escrito is False
    assert archivo.stat().st_mtime_ns == mtime


def test_save_page_reescribe_si_cambia(tmp_path: Path) -> None:
    """Contenido distinto → se reescribe y no quedan temporales."""
    storage = RawStorage(tmp_path)
    storage.save_page(URL, b"<html>v1</html>")

    relativa, hash_, escrito = storage.save_page(URL, b"<html>v2</html>")

    assert escrito is True
    assert (tmp_path / relativa).read_bytes() == b"<html>v2</html>"
    assert hash_ == sha256(b"<html>v2</html>")
    assert not list(storage.pages_dir.glob(".*.tmp"))


def test_huella_de_texto_ignora_cambios_volatiles_del_marcado(tmp_path: Path) -> None:
    """Con text_fingerprint, ids aleatorios o scripts distintos no cuentan como cambio."""
    storage = RawStorage(tmp_path, fingerprint=text_fingerprint)
    v1 = b'<html><script data-rpid="111"></script><link id="P.abc"><p>Tasa 1%</p></html>'
    v2 = b'<html><script data-rpid="999"></script><link id="I-xyz"><p>Tasa 1%</p></html>'
    v3 = b'<html><script data-rpid="999"></script><link id="I-xyz"><p>Tasa 2%</p></html>'
    relativa, huella1, _ = storage.save_page(URL, v1)

    _, huella2, escrito2 = storage.save_page(URL, v2)
    _, huella3, escrito3 = storage.save_page(URL, v3)

    assert huella1 == huella2 == text_fingerprint(v1)
    assert escrito2 is False
    assert escrito3 is True
    assert huella3 != huella1
    assert (tmp_path / relativa).read_bytes() == v3


def test_manifest_round_trip_jsonl(tmp_path: Path) -> None:
    """Cada entrada es una línea JSON válida y se relee igual."""
    storage = RawStorage(tmp_path)
    entradas = [_entrada(), _entrada(f"{URL}/2", outcome="error_http", status=404)]

    total = storage.write_manifest(entradas)

    lineas = storage.manifest_path.read_text("utf-8").splitlines()
    assert total == 2
    assert [json.loads(linea)["url"] for linea in lineas] == [URL, f"{URL}/2"]
    assert storage.load_manifest() == {e.url: e for e in entradas}


def test_manifest_se_fusiona_con_el_anterior(tmp_path: Path) -> None:
    """Un crawl parcial actualiza sus URLs y conserva las demás."""
    storage = RawStorage(tmp_path)
    storage.write_manifest([_entrada(), _entrada(f"{URL}/vieja")])

    storage.write_manifest([_entrada(outcome="sin_cambios")])

    manifest = storage.load_manifest()
    assert set(manifest) == {URL, f"{URL}/vieja"}
    assert manifest[URL].outcome == "sin_cambios"


def test_manifest_ignora_lineas_invalidas(tmp_path: Path) -> None:
    """Una línea corrupta no impide leer el resto."""
    storage = RawStorage(tmp_path)
    storage.write_manifest([_entrada()])
    with storage.manifest_path.open("a", encoding="utf-8") as f:
        f.write("{no es json\n\n")

    assert list(storage.load_manifest()) == [URL]


def test_manifest_inexistente_es_vacio(tmp_path: Path) -> None:
    """Sin manifest previo se devuelve un dict vacío."""
    assert RawStorage(tmp_path / "nuevo").load_manifest() == {}


def test_outcome_invalido_es_rechazado() -> None:
    """El esquema del manifest valida el campo outcome."""
    with pytest.raises(ValueError, match="outcome"):
        _entrada(outcome="inventado")
