"""Pruebas del pipeline de limpieza completo y del chequeo de fugas (M3, sin red)."""

import hashlib
import json
from pathlib import Path

import pytest

from rag_bbva.exceptions import ProcessingError
from rag_bbva.processing.models import CleanDocument
from rag_bbva.processing.pipeline import (
    CleaningPipeline,
    read_raw_pages,
    write_clean_output,
)
from rag_bbva.processing.quality import LEAK_PATTERNS, find_leaks, leak_report
from rag_bbva.scraping.page_analysis import visible_text
from rag_bbva.scraping.storage import ManifestEntry

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"
B = "https://www.bancolombia.com"
FECHA = "2026-10-02T03:27:07+00:00"

# (URL pedida, URL final, fixture, lastmod)
PAGINAS = [
    (f"{B}/personas/canales/cajeros", None, "plantilla_a_cajeros", "2026-06-01T14:30:00-05:00"),
    (f"{B}/acerca-de/documentos-legales/gmf-iva", None, "plantilla_b_gmf_iva", None),
    (f"{B}/acerca-de/contactanos", None, "plantilla_b_contactanos_lrp_error", None),
    (
        f"{B}/educacion-financiera/finanzas-personales/organiza-tus-deudas",
        None,
        "plantilla_c_organiza_deudas",
        "2026-06-01T14:30:00-05:00",
    ),
    # Soft-404: otra URL que sirve exactamente la misma página que gmf-iva.
    (f"{B}/acerca-de/documentos-legales/gmf-iva-antiguo", None, "plantilla_b_gmf_iva", None),
    # Redirección interna: se guarda con su URL final.
    (
        f"{B}/empresas/cajeros",
        f"{B}/personas/canales/cajeros-empresas",
        "plantilla_a_cajeros",
        None,
    ),
]


def _entrada(url: str, **campos: object) -> ManifestEntry:
    base: dict[str, object] = {
        "url": url, "final_url": url, "status": 200, "outcome": "guardada",
        "fetched_at": FECHA, "depth": 0, "source": "sitemap",
    }  # fmt: skip
    return ManifestEntry.model_validate(base | campos)


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    """`data/raw` simulado: HTML de los fixtures + manifest con todos los casos."""
    raw = tmp_path / "raw"
    (raw / "pages").mkdir(parents=True)
    entradas = []
    for url, final, fixture, lastmod in PAGINAS:
        final = final or url
        ruta = f"pages/{hashlib.sha1(final.encode()).hexdigest()}.html"
        (raw / ruta).write_bytes((FIXTURES / f"{fixture}.html").read_bytes())
        entradas.append(_entrada(url, final_url=final, path=ruta, lastmod=lastmod))
    entradas += [
        _entrada(f"{B}/empresas", final_url=f"{B}/negocios", outcome="duplicada"),
        _entrada(f"{B}/negocios/aliados/x", status=403, outcome="error_http"),
        _entrada(f"{B}/acerca-de/sala-prensa/n", status=None, outcome="excluida"),
        _entrada(f"{B}/personas/perdida", path="pages/no-existe.html"),
    ]
    (raw / "manifest.jsonl").write_text(
        "".join(e.model_dump_json() + "\n" for e in entradas), encoding="utf-8"
    )
    return raw


def _pipeline() -> CleaningPipeline:
    return CleaningPipeline.default(min_chars=200, min_extraction_coverage=0.9)


# ---------------------------------------------------------------- lectura del manifest


def test_lee_solo_entradas_con_html_y_url_final(raw_dir: Path) -> None:
    lectura = read_raw_pages(raw_dir)

    urls = [p.url for p in lectura.pages]
    assert f"{B}/personas/canales/cajeros-empresas" in urls  # URL canónica = final_url
    assert f"{B}/empresas/cajeros" not in urls
    assert f"{B}/negocios" not in urls
    assert lectura.skipped == {
        "duplicada": 1,
        "sin_html (error_http)": 1,
        "sin_html (excluida)": 1,
    }
    assert urls == sorted(urls, key=lambda u: (len(u), u))


def test_sin_manifest_lanza_processing_error(tmp_path: Path) -> None:
    with pytest.raises(ProcessingError, match="scrape"):
        read_raw_pages(tmp_path)


# ---------------------------------------------------------------- pipeline completo


def test_documentos_y_metadatos(raw_dir: Path) -> None:
    resultado = _pipeline().process_directory(raw_dir)
    docs = {d.url: d for d in resultado.documents}

    cajeros = docs[f"{B}/personas/canales/cajeros"]
    assert cajeros.doc_id == hashlib.sha1(cajeros.url.encode()).hexdigest()
    assert cajeros.section == "personas"
    assert cajeros.template == "A_main"
    assert cajeros.breadcrumbs == ["Inicio", "Canales", "Cajeros"]
    assert cajeros.html_lang == "es"
    assert cajeros.lang == "es"
    assert cajeros.lastmod == "2026-06-01T14:30:00-05:00"
    assert cajeros.scraped_at == FECHA
    assert cajeros.published_at is None
    assert cajeros.n_chars == len(cajeros.text)
    assert cajeros.content_hash == hashlib.sha256(cajeros.text.encode()).hexdigest()
    assert cajeros.text.startswith("# Cajeros\n\n")

    deudas = docs[f"{B}/educacion-financiera/finanzas-personales/organiza-tus-deudas"]
    assert deudas.section == "educacion-financiera"
    assert deudas.template == "C_role_main"
    assert (deudas.html_lang, deudas.lang) == ("en", "es")
    assert "## Refinanciar" in deudas.text

    assert docs[f"{B}/acerca-de/documentos-legales/gmf-iva"].template == "B_main_content"


def test_descartes_con_motivo(raw_dir: Path) -> None:
    """Texto corto (solo errores de WCM), soft-404 duplicado y HTML ausente."""
    reporte = _pipeline().process_directory(raw_dir).report
    motivos = {d.url: (d.reason, d.detail) for d in reporte.discarded_documents}

    assert motivos[f"{B}/acerca-de/contactanos"][0] == "texto_corto"
    assert motivos[f"{B}/acerca-de/documentos-legales/gmf-iva-antiguo"] == (
        "texto_duplicado",
        f"mismo texto que {B}/acerca-de/documentos-legales/gmf-iva",
    )
    assert motivos[f"{B}/personas/perdida"] == ("html_no_encontrado", "pages/no-existe.html")
    # cajeros-empresas tiene el mismo HTML que cajeros: duplicado de la URL más corta.
    assert motivos[f"{B}/personas/canales/cajeros-empresas"][0] == "texto_duplicado"
    assert reporte.discarded == {"texto_duplicado": 2, "html_no_encontrado": 1, "texto_corto": 1}


def test_reporte(raw_dir: Path) -> None:
    reporte = _pipeline().process_directory(raw_dir).report

    assert reporte.processed == 7
    assert reporte.kept == 3
    assert reporte.by_template == {"A_main": 1, "B_main_content": 1, "C_role_main": 1}
    assert reporte.by_section == {"acerca-de": 1, "educacion-financiera": 1, "personas": 1}
    assert sum(reporte.by_extraction.values()) == 3
    assert reporte.n_chars is not None
    assert reporte.n_chars.min <= reporte.n_chars.p50 <= reporte.n_chars.p95 <= reporte.n_chars.max
    assert reporte.leaks.total == 0
    assert reporte.by_lang == {"es": 3}
    assert reporte.lang_mismatch == 1  # la página de la plantilla C
    assert reporte.lang_mismatch_pairs == {"en → es": 1}
    assert reporte.lang_fallback_by_template == {}  # los tres fixtures tienen señal clara


def test_determinista(raw_dir: Path, tmp_path: Path) -> None:
    """Misma entrada → misma salida, byte a byte (documentos y reporte)."""
    salidas = []
    for i in range(2):
        destino = tmp_path / f"clean{i}"
        write_clean_output(_pipeline().process_directory(raw_dir), destino)
        salidas.append(
            (
                (destino / "documents.jsonl").read_bytes(),
                (destino / "clean_report.json").read_bytes(),
            )
        )

    assert salidas[0] == salidas[1]
    lineas = salidas[0][0].decode().splitlines()
    assert len(lineas) == 3
    assert all(CleanDocument.model_validate_json(linea) for linea in lineas)
    assert json.loads(salidas[0][1])["kept"] == 3


def test_reutilizar_el_pipeline_reinicia_la_deduplicacion(raw_dir: Path) -> None:
    pipeline = _pipeline()

    primero = pipeline.process_directory(raw_dir).report.kept
    segundo = pipeline.process_directory(raw_dir).report.kept

    assert primero == segundo == 3


def test_pipeline_sin_pasos_es_un_error() -> None:
    with pytest.raises(ProcessingError):
        CleaningPipeline([])


# ---------------------------------------------------------------- fugas de boilerplate


@pytest.mark.parametrize(
    "fixture",
    ["plantilla_a_cajeros", "plantilla_b_gmf_iva", "plantilla_c_organiza_deudas"],
)
def test_fixtures_sin_fugas_de_boilerplate(raw_dir: Path, fixture: str) -> None:
    """Meta: 0 fugas en los documentos limpios de los fixtures, aunque el HTML crudo sí
    las tenga (el chequeo no es trivialmente cero)."""
    crudo = visible_text((FIXTURES / f"{fixture}.html").read_text("utf-8"))
    assert find_leaks(crudo), "el fixture crudo debería tener boilerplate"

    docs = _pipeline().process_directory(raw_dir).documents
    url = next(u for u, _, f, _ in PAGINAS if f == fixture)
    doc = next(d for d in docs if d.url == url)

    assert find_leaks(doc.text) == {}


@pytest.mark.parametrize(
    ("patron", "texto"),
    [
        ("placeholder", "Menú ${title} ${loading}"),
        ("menu_portlet", "Web Content Viewer Component Action Menu"),
        ("error_wcm", "Warning Invalid configuration found."),
        ("icono", "Inicio arrow2-right Canales"),
        ("relacionados", "texto\nContenido relacionado\nmás texto"),
        ("cookies", "Usamos cookies para mejorar tu experiencia"),
        ("pie", "Copyright © 2026 Bancolombia S.A."),
        ("menu_sitio", "Sucursal Virtual Personas Sucursal Virtual Negocios Pagos PSE"),
        ("menu_sitio", "Personas Negocios Corporativos Buscar"),
        ("pie", "Síguenos en nuestras redes sociales"),
    ],
)
def test_cada_patron_de_fuga_detecta_su_caso(patron: str, texto: str) -> None:
    assert patron in LEAK_PATTERNS
    assert patron in find_leaks(texto)


def test_leak_report_cuenta_casos() -> None:
    doc = CleanDocument(
        doc_id="1", url=f"{B}/x", title=None, section="personas", breadcrumbs=[],
        text="Hola ${title} y ${loading}. Copyright © 2026", html_lang="es", lang="es",
        lang_source="html_lang",
        lastmod=None,
        published_at=None, scraped_at=FECHA, content_hash="h", n_chars=10,
        template="otra", extraction="selector",
    )  # fmt: skip

    reporte = leak_report([doc], max_cases=2)

    assert reporte.total == 3
    assert reporte.documents_with_leaks == 1
    assert reporte.by_pattern == {"pie": 1, "placeholder": 2}
    assert len(reporte.cases) == 2


def test_reporte_cuenta_el_respaldo_de_idioma_por_plantilla(tmp_path: Path) -> None:
    """Una página sin palabras funcionales toma `lang` de `<html lang>` y se cuenta."""
    raw = tmp_path / "raw"
    (raw / "pages").mkdir(parents=True)
    filas = "".join(f"<li>Plan {i}: $14.900 / Mes · Retiros $2.700</li>" for i in range(8))
    html = f'<html lang="es-CO"><body><main><ul>{filas}</ul></main></body></html>'
    (raw / "pages" / "t.html").write_text(html, encoding="utf-8")
    (raw / "manifest.jsonl").write_text(
        _entrada(f"{B}/personas/tarifas", path="pages/t.html").model_dump_json() + "\n",
        encoding="utf-8",
    )

    resultado = _pipeline().process_directory(raw)

    doc = resultado.documents[0]
    assert (doc.lang, doc.lang_source) == ("es", "html_lang")
    assert resultado.report.lang_fallback_by_template == {"A_main": {"es": 1}}
