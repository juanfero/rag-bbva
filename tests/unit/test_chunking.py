"""Pruebas del chunking (M4, sin red ni modelos)."""

import json
import re
from itertools import pairwise
from pathlib import Path

import pytest

from rag_bbva.exceptions import IndexingError
from rag_bbva.indexing.chunking import (
    FixedSizeChunker,
    HeadingAwareChunker,
    context_header,
    split_sections,
    split_text,
)
from rag_bbva.processing.models import CleanDocument

FIXTURES = Path(__file__).parent.parent / "fixtures"
B = "https://www.bancolombia.com"
_PALABRA = re.compile(r"\S+")


def _doc(
    texto: str, title: str | None = "Cajeros automáticos", url: str = f"{B}/personas/x"
) -> CleanDocument:
    return CleanDocument(
        doc_id="d" + str(abs(hash(url)))[:8], url=url, title=title, section="personas",
        breadcrumbs=[], text=texto, html_lang="es", lang="es", lang_source="detectado",
        lastmod=None, published_at=None, scraped_at="2026-10-02T00:00:00+00:00",
        content_hash="h", n_chars=len(texto), template="A_main", extraction="selector",
    )  # fmt: skip


@pytest.fixture(scope="module")
def glosario() -> CleanDocument:
    """Documento limpio real más largo del sitio (116 021 caracteres)."""
    return CleanDocument.model_validate(
        json.loads((FIXTURES / "clean" / "glosario.json").read_text("utf-8"))
    )


def _solapamiento(anterior: str, siguiente: str) -> int:
    """Largo del sufijo más largo de `anterior` que es prefijo de `siguiente`."""
    for largo in range(min(len(anterior), len(siguiente)), 0, -1):
        if anterior.endswith(siguiente[:largo]):
            return largo
    return 0


# ---------------------------------------------------------------- split_text


TEXTO = " ".join(f"palabra{i:03d}" for i in range(300))  # 3 299 caracteres


def test_split_text_respeta_el_tamano_y_no_corta_palabras() -> None:
    piezas = split_text(TEXTO, 200, 40)

    palabras = set(_PALABRA.findall(TEXTO))
    assert all(len(p) <= 200 for p in piezas)
    assert all(set(_PALABRA.findall(p)) <= palabras for p in piezas)  # ninguna palabra partida
    assert set().union(*(_PALABRA.findall(p) for p in piezas)) == palabras  # no se pierde nada


def test_split_text_solapamiento() -> None:
    """Cada pieza empieza con hasta `overlap` caracteres del final de la anterior."""
    piezas = split_text(TEXTO, 200, 40)

    for anterior, siguiente in pairwise(piezas):
        assert 0 < _solapamiento(anterior, siguiente) <= 40


def test_split_text_prefiere_cortar_en_parrafo() -> None:
    texto = "a " * 70 + "\n\n" + "b " * 70  # el párrafo corta en la posición ~140
    piezas = split_text(texto, 200, 0)

    assert piezas[0] == ("a " * 70).strip()


def test_split_text_sin_solapamiento_y_texto_corto() -> None:
    assert split_text("hola mundo", 200, 0) == ["hola mundo"]
    assert split_text("   ", 200, 0) == []


def test_split_text_solapamiento_invalido() -> None:
    with pytest.raises(IndexingError):
        split_text(TEXTO, 100, 100)


# ---------------------------------------------------------------- secciones y heading_path


MARKDOWN = """# Cajeros

Te acompañamos en cada rincón de Colombia.

## Beneficios

### Maneja tu cuenta

Cambia tu clave y revisa saldos.

### Aprovéchalos 24/7

Úsalos de día o de noche.

## Tarifas y topes

Valores iniciales de los cajeros.
"""


def test_split_sections_rutas_de_titulos() -> None:
    rutas = [s.ruta for s in split_sections(MARKDOWN)]

    assert rutas == [
        ("Cajeros",),
        ("Cajeros", "Beneficios"),
        ("Cajeros", "Beneficios", "Maneja tu cuenta"),
        ("Cajeros", "Beneficios", "Aprovéchalos 24/7"),
        ("Cajeros", "Tarifas y topes"),
    ]


def test_heading_aware_agrupa_secciones_hermanas_con_su_ruta_comun() -> None:
    """Con tamaño suficiente, las subsecciones de "Beneficios" van juntas y el chunk
    lleva la ruta común; el texto conserva los títulos markdown."""
    chunker = HeadingAwareChunker(chunk_size=120, chunk_overlap=20)

    chunks = chunker.chunk(_doc(MARKDOWN))

    beneficios = next(c for c in chunks if "Maneja tu cuenta" in c.text)
    assert beneficios.heading_path == "Cajeros > Beneficios"
    assert "Aprovéchalos 24/7" in beneficios.text
    assert beneficios.text.startswith("## Beneficios")
    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_heading_aware_parte_secciones_grandes_con_su_ruta() -> None:
    cuerpo = " ".join(f"tarifa{i}" for i in range(200))
    chunks = HeadingAwareChunker(300, 50).chunk(_doc(f"# Cajeros\n\n## Tarifas\n\n{cuerpo}"))

    grandes = [c for c in chunks if "tarifa199" in c.text or "tarifa0 " in c.text]
    assert all(c.heading_path == "Cajeros > Tarifas" for c in grandes)
    assert all(c.n_chars <= 300 for c in chunks)
    assert len(chunks) > 3


def test_titulo_suelto_se_antepone_a_la_seccion_grande() -> None:
    """Un título sin texto antes de una sección que no cabe no queda como chunk aparte."""
    cuerpo = " ".join(f"valor{i}" for i in range(100))
    texto = f"# Tarifario\n\n## Cuentas y depósitos\n\n### Cuota de manejo\n\n{cuerpo}"

    chunks = HeadingAwareChunker(300, 50).chunk(_doc(texto))

    assert chunks[0].text.startswith("# Tarifario\n\n## Cuentas y depósitos\n\n### Cuota de manejo")
    assert all(c.n_chars >= 100 for c in chunks[:-1])


def test_chunks_solo_de_titulos_se_descartan() -> None:
    texto = "# Ayuda\n\n" + "Texto de la respuesta. " * 30 + "\n\n### Llámanos\n\n### Chat"

    chunks = HeadingAwareChunker(300, 50).chunk(_doc(texto))

    assert chunks
    assert all(re.search(r"^(?!#)\S", c.text, re.M) for c in chunks)  # cada uno tiene texto


def test_ruta_con_titulo_del_documento_si_no_abre_con_h1() -> None:
    chunks = HeadingAwareChunker(800, 100).chunk(_doc("## Requisitos\n\nCédula y formulario."))

    assert chunks[0].heading_path == "Cajeros automáticos > Requisitos"


def test_encabezado_de_contexto_en_el_texto_a_embeber() -> None:
    chunk = HeadingAwareChunker(120, 20).chunk(_doc(MARKDOWN))[1]

    assert chunk.embedding_text.startswith("Cajeros automáticos | personas\nCajeros > ")
    assert chunk.embedding_text.endswith(chunk.text)
    assert context_header(_doc("x"), "Cajeros automáticos") == "Cajeros automáticos | personas\n\n"


# ---------------------------------------------------------------- invariantes


@pytest.mark.parametrize("chunker", [HeadingAwareChunker(800, 120), FixedSizeChunker(800, 120)])
def test_glosario_se_trocea_bien(glosario: CleanDocument, chunker: HeadingAwareChunker) -> None:
    """El documento real más largo: ningún chunk vacío ni mayor que el tamaño, metadatos
    completos y todas las palabras del documento en algún chunk."""
    chunks = chunker.chunk(glosario)

    assert 145 <= len(chunks) <= 300  # 116 021 caracteres / 800 ≈ 145
    assert all(0 < c.n_chars <= 800 for c in chunks)
    assert all(c.text.strip() and c.n_chars == len(c.text) for c in chunks)
    assert {c.doc_id for c in chunks} == {glosario.doc_id}
    assert [c.position for c in chunks] == list(range(len(chunks)))
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    palabras = set(_PALABRA.findall(glosario.text)) - {"##", "###"}
    vistas = set().union(*(_PALABRA.findall(c.text) for c in chunks))
    assert palabras <= vistas


def test_glosario_heading_path_por_termino(glosario: CleanDocument) -> None:
    chunks = HeadingAwareChunker(800, 120).chunk(glosario)

    caja = next(c for c in chunks if "### Caja\n" in c.text)
    assert caja.heading_path.startswith("Glosario personas Bancolombia > C")
    assert all(c.heading_path.startswith("Glosario personas Bancolombia") for c in chunks)


def test_ids_estables_y_distintos_por_estrategia(glosario: CleanDocument) -> None:
    primero = HeadingAwareChunker(800, 120).chunk(glosario)
    segundo = HeadingAwareChunker(800, 120).chunk(glosario)
    fijo = FixedSizeChunker(800, 120).chunk(glosario)

    assert [c.chunk_id for c in primero] == [c.chunk_id for c in segundo]
    assert {c.chunk_id for c in primero}.isdisjoint(c.chunk_id for c in fijo)


def test_fixed_size_ignora_titulos() -> None:
    chunks = FixedSizeChunker(120, 20).chunk(_doc(MARKDOWN))

    assert all(c.heading_path == "Cajeros automáticos" for c in chunks)
    assert all(c.n_chars <= 120 for c in chunks)
    assert chunks[0].chunker == "fixed_size"


def test_overlap_mayor_o_igual_al_tamano_es_un_error() -> None:
    with pytest.raises(IndexingError):
        HeadingAwareChunker(100, 100)


def test_ningun_chunk_supera_el_tamano_con_titulos_arrastrados() -> None:
    """Títulos vacíos al final de un grupo pasan al siguiente sin superar el tamaño."""
    partes = []
    for i in range(30):
        partes.append(f"## Tema {i}\n\n### Detalle {i}\n\n" + f"texto{i} " * (5 + i % 7))
        partes.append(f"## Vacío {i}")
    chunks = HeadingAwareChunker(150, 30).chunk(_doc("\n\n".join(partes)))

    assert all(c.n_chars <= 150 for c in chunks)
    assert not any(c.text.rstrip().endswith(("## Vacío 3", "## Vacío 4")) for c in chunks)
