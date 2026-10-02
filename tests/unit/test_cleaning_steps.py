"""Pruebas de cada paso de la limpieza por separado (M3, sin red).

Los fixtures `plantilla_*.html` son páginas reales de M2 recortadas con
`scripts/trim_html_fixture.py` (una por plantilla del sitio).
"""

from pathlib import Path

import pytest

from rag_bbva.exceptions import ProcessingError
from rag_bbva.processing import steps
from rag_bbva.processing.markdown import html_to_markdown
from rag_bbva.processing.models import Discarded, RawPage, WorkingDocument
from rag_bbva.processing.steps import (
    CleaningStep,
    DeduplicateStep,
    DetectLanguageStep,
    ExtractMainContentStep,
    ExtractMetadataStep,
    MinLengthFilterStep,
    NormalizeTextStep,
    ParseHtmlStep,
    RemoveBoilerplateStep,
    detect_language,
    normalize_text,
    primary_language,
    word_coverage,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"
B = "https://www.bancolombia.com"


def _fixture(nombre: str) -> str:
    return (FIXTURES / f"{nombre}.html").read_text("utf-8")


def _doc(html: str, url: str = f"{B}/personas/x") -> WorkingDocument:
    pagina = RawPage(url=url, source_url=url, path="p.html", fetched_at="2026-10-02", html=html)
    return WorkingDocument(page=pagina)


def _hasta(html: str, *pasos: CleaningStep) -> WorkingDocument:
    """Aplica en orden los pasos dados y exige que ninguno descarte."""
    doc: WorkingDocument | Discarded = _doc(html)
    for paso in pasos:
        assert isinstance(doc, WorkingDocument)
        doc = paso.process(doc)
    assert isinstance(doc, WorkingDocument)
    return doc


def _limpio(nombre: str) -> WorkingDocument:
    return _hasta(_fixture(nombre), ParseHtmlStep(), ExtractMetadataStep(), RemoveBoilerplateStep())


# ---------------------------------------------------------------- parseo y metadatos


def test_parse_html_sin_body_se_descarta() -> None:
    assert ParseHtmlStep().process(_doc("")) == Discarded("html_sin_body")


def test_paso_fuera_de_orden_lanza_processing_error() -> None:
    with pytest.raises(ProcessingError):
        ExtractMetadataStep().process(_doc("<html><body>x</body></html>"))


@pytest.mark.parametrize(
    ("fixture", "plantilla", "html_lang", "titulo"),
    [
        ("plantilla_a_cajeros", "A_main", "es", "Cajeros automáticos"),
        ("plantilla_b_gmf_iva", "B_main_content", "es", "GMF e IVA"),
        ("plantilla_c_organiza_deudas", "C_role_main", "en", "Aprende a organizar tus deudas"),
    ],
)
def test_metadatos_de_las_tres_plantillas(
    fixture: str, plantilla: str, html_lang: str, titulo: str
) -> None:
    """Plantilla detectada, `<html lang>` tal como lo declara el sitio y título."""
    doc = _hasta(_fixture(fixture), ParseHtmlStep(), ExtractMetadataStep())

    assert doc.template == plantilla
    assert doc.html_lang == html_lang
    assert doc.title == titulo


def test_migas_de_pan_sin_nombres_de_iconos() -> None:
    """Plantilla A: las migas salen de `.bc-breadcrumb-items` sin "arrow2-right"."""
    doc = _hasta(_fixture("plantilla_a_cajeros"), ParseHtmlStep(), ExtractMetadataStep())

    assert doc.breadcrumbs == ["Inicio", "Canales", "Cajeros"]


def test_plantilla_otra_y_sin_migas() -> None:
    doc = _hasta(
        "<html><body><div>Hola</div></body></html>", ParseHtmlStep(), ExtractMetadataStep()
    )

    assert doc.template == "otra"
    assert doc.breadcrumbs == []
    assert doc.published_at is None
    assert doc.html_lang is None


_LD = '<script type="application/ld+json">{}</script>'


@pytest.mark.parametrize(
    ("cabeza", "esperado"),
    [
        (
            '<meta property="article:published_time" content=" 2024-05-01T10:00:00-05:00 ">',
            "2024-05-01T10:00:00-05:00",
        ),
        (_LD.format('{"@type": "NewsArticle", "datePublished": "2024-06-02"}'), "2024-06-02"),
        (_LD.format('[{"@type": "Org"}, {"datePublished": "2024-07-03"}]'), "2024-07-03"),
        (_LD.format("no es json"), None),
        ("", None),
    ],
)
def test_published_at_solo_de_metadatos(cabeza: str, esperado: str | None) -> None:
    """`published_at` sale de `article:published_time` o JSON-LD; si no, `None`."""
    html = f"<html><head>{cabeza}</head><body><p>Febrero 22, 2021</p></body></html>"

    doc = _hasta(html, ParseHtmlStep(), ExtractMetadataStep())

    assert doc.published_at == esperado


def test_titulo_sin_sufijo_de_marca_y_con_h1_de_respaldo() -> None:
    con_sufijo = "<html><head><title>Cuentas | Bancolombia</title></head><body></body></html>"
    sin_title = "<html><body><h1> Créditos  de vivienda </h1></body></html>"

    assert _hasta(con_sufijo, ParseHtmlStep(), ExtractMetadataStep()).title == "Cuentas"
    assert _hasta(sin_title, ParseHtmlStep(), ExtractMetadataStep()).title == "Créditos de vivienda"


# ---------------------------------------------------------------- boilerplate


def test_boilerplate_plantilla_c_menus_placeholders_y_relacionados() -> None:
    """WebSphere: sin cabecera, pie, menús de portlet, `${…}` ni contenido relacionado (L-08)."""
    crudo = _fixture("plantilla_c_organiza_deudas")
    assert "${title}" in crudo
    assert "miniatura-articulos" in crudo

    doc = _limpio("plantilla_c_organiza_deudas")
    soup = doc.soup
    assert soup is not None
    texto = soup.get_text(" ")

    for selector in ("header", "nav", "footer", ".wpthemeHiddenPlusControlHeaderParent",
                     "section.miniatura-articulos", "#portletState"):  # fmt: skip
        assert soup.select_one(selector) is None, selector
    assert "${" not in texto
    assert "Contenido relacionado" not in texto
    assert "Cómo utilizar saludablemente una tarjeta de crédito" not in texto  # artículo rotativo
    assert "Refinanciar" in texto  # el contenido propio se conserva


def test_boilerplate_plantilla_a_iconos_y_navegacion() -> None:
    doc = _limpio("plantilla_a_cajeros")
    assert doc.soup is not None
    texto = doc.soup.get_text(" ")

    assert "arrow2-right" not in texto
    assert "menu-dots-v" not in texto
    assert "Te puede interesar" not in texto
    assert "Copyright" not in texto
    assert "Tarifas y topes" in texto


def test_boilerplate_plantilla_b_cajas_de_error_wcm() -> None:
    crudo = _fixture("plantilla_b_contactanos_lrp_error")
    assert "lrpError" in crudo

    doc = _limpio("plantilla_b_contactanos_lrp_error")
    assert doc.soup is not None

    assert doc.soup.select_one(".lrpError") is None
    assert "Invalid configuration found" not in doc.soup.get_text(" ")


def test_boilerplate_banner_de_cookies_y_textos_de_interfaz() -> None:
    html = (
        "<html><body><main><div id='CookieBanner'>Usamos cookies. Aceptar cookies</div>"
        "<p>Contenido real del producto.</p><a>Leer más</a><span> Comparte este articulo </span>"
        "</main></body></html>"
    )

    doc = _hasta(html, ParseHtmlStep(), ExtractMetadataStep(), RemoveBoilerplateStep())
    assert doc.soup is not None
    texto = " ".join(doc.soup.get_text(" ").split())

    assert texto == "Contenido real del producto."


# ---------------------------------------------------------------- contenido principal


def test_contenedor_por_orden_de_selectores() -> None:
    """Si hay `main` y `[role=main]`, gana `main`; sin ninguno, `body`."""
    con_main = (
        "<html><body><div role='main'><p>Rol</p></div><main><p>Principal</p></main></body></html>"
    )
    solo_body = "<html><body><p>Cuerpo</p></body></html>"
    paso = ExtractMainContentStep(min_coverage=0.9)

    doc = _hasta(con_main, ParseHtmlStep(), ExtractMetadataStep(), RemoveBoilerplateStep(), paso)
    assert doc.container is not None and doc.container.name == "main"
    doc = _hasta(solo_body, ParseHtmlStep(), ExtractMetadataStep(), RemoveBoilerplateStep(), paso)
    assert doc.container is not None and doc.container.name == "body"


def test_usa_trafilatura_si_conserva_el_contenido() -> None:
    """Plantilla B: trafilatura conserva el vocabulario del contenedor y se usa."""
    doc = _hasta(
        _fixture("plantilla_b_gmf_iva"),
        ParseHtmlStep(), ExtractMetadataStep(), RemoveBoilerplateStep(),
        ExtractMainContentStep(min_coverage=0.9),
    )  # fmt: skip

    assert doc.extraction == "trafilatura"
    assert "## IVA" in doc.text


def test_fallback_por_selector_si_trafilatura_extrae_poco(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si trafilatura omite parte del contenido, se usa el contenedor por selector."""
    monkeypatch.setattr(steps.trafilatura, "extract", lambda *a, **k: "Solo una frase.")

    doc = _hasta(
        _fixture("plantilla_b_gmf_iva"),
        ParseHtmlStep(), ExtractMetadataStep(), RemoveBoilerplateStep(),
        ExtractMainContentStep(min_coverage=0.9),
    )  # fmt: skip

    assert doc.extraction == "selector"
    assert doc.text.startswith("# GMF e IVA")
    assert "## IVA" in doc.text


def test_fallback_si_trafilatura_no_devuelve_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steps.trafilatura, "extract", lambda *a, **k: None)
    html = "<html><body><main><h2>Título</h2><p>Texto del cuerpo.</p></main></body></html>"

    doc = _hasta(html, ParseHtmlStep(), ExtractMetadataStep(), RemoveBoilerplateStep(),
                 ExtractMainContentStep(min_coverage=0.9))  # fmt: skip

    assert doc.extraction == "selector"
    assert doc.text == "## Título\n\nTexto del cuerpo."


def test_word_coverage() -> None:
    assert word_coverage("cuenta ahorro plata", "cuenta de ahorro") == pytest.approx(2 / 3)
    assert word_coverage("", "algo") == 1.0


# ---------------------------------------------------------------- markdown


def test_markdown_titulos_listas_y_tablas() -> None:
    html = (
        "<div><h1>Tarifas</h1><p>Valores <b>vigentes</b>.</p>"
        "<ul><li>Uno</li><li> Dos </li></ul>"
        "<table><tr><th>Canal</th><th>Tope</th></tr><tr><td>Cajero</td><td>$2.700.000</td></tr></table>"
        "<h3>Notas</h3>texto suelto<br>otra línea</div>"
    )
    from bs4 import BeautifulSoup

    salida = html_to_markdown(BeautifulSoup(html, "lxml").div)

    assert salida == (
        "# Tarifas\n\nValores vigentes .\n\n- Uno\n- Dos\n\n"
        "| Canal | Tope |\n| --- | --- |\n| Cajero | $2.700.000 |\n\n"
        "### Notas\n\ntexto suelto\n\notra línea"
    )


def test_markdown_tabla_de_maquetacion_se_recorre_como_contenido() -> None:
    from bs4 import BeautifulSoup

    html = "<div><table><tr><td><h2>Contáctanos</h2><p>Escríbenos.</p></td></tr></table></div>"

    assert html_to_markdown(BeautifulSoup(html, "lxml").div) == "## Contáctanos\n\nEscríbenos."


# ---------------------------------------------------------------- normalización y filtros


def test_normalize_text() -> None:
    """NFC, sin invisibles ni espacios no separables, una línea en blanco como máximo y
    sin bloques repetidos (consecutivos o largos)."""
    largo = "Chatea con nosotros en WhatsApp al número que aparece en la página de contacto."
    crudo = (
        "  Informacio\u0301n\u00a0 de  la\u200b cuenta  \n\n\n\n## Título\n## Título\n\n"
        f"Corto\n\nCorto\n\nOtro\n\nCorto\n\n{largo}\n\nMedio\n\n{largo}\n"
    )

    assert normalize_text(crudo) == (
        "Informaci\u00f3n de la cuenta\n\n## Título\n## Título\n\n"
        f"Corto\n\nOtro\n\nCorto\n\n{largo}\n\nMedio"
    )


def test_normalize_step_aplica_normalize_text() -> None:
    doc = _doc("")
    doc.text = "a  b\n\n\n\nc"

    assert NormalizeTextStep().process(doc).text == "a b\n\nc"  # type: ignore[union-attr]


# ---------------------------------------------------------------- idioma


def test_plantilla_c_declara_en_pero_el_contenido_es_espanol() -> None:
    """WebSphere declara `<html lang="en">` en páginas en español: `html_lang` conserva
    el valor declarado y `lang` refleja el idioma real del contenido."""
    doc = _hasta(
        _fixture("plantilla_c_organiza_deudas"),
        ParseHtmlStep(), ExtractMetadataStep(), RemoveBoilerplateStep(),
        ExtractMainContentStep(min_coverage=0.9), NormalizeTextStep(), DetectLanguageStep(),
    )  # fmt: skip

    assert doc.html_lang == "en"
    assert doc.lang == "es"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Abre tu cuenta de ahorros y mueve la plata desde la app con tu clave.", "es"),
        ("Open your savings account and move the money from the app with your key.", "en"),
        ("Cuenta Plan Oro $14,900", None),  # sin palabras funcionales suficientes
        ("de la que el en the and of to in is", None),  # empate: sin señal clara
        ("", None),
    ],
)
def test_detect_language(texto: str, esperado: str | None) -> None:
    assert detect_language(texto) == esperado


def test_sin_senal_clara_usa_html_lang() -> None:
    doc = _doc("")
    doc.html_lang, doc.text = "es-CO", "Plan Oro $14,900"

    assert DetectLanguageStep().process(doc).lang == "es"  # type: ignore[union-attr]
    doc.html_lang = None
    assert DetectLanguageStep().process(doc).lang is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("etiqueta", "esperado"), [("es-CO", "es"), ("EN", "en"), ("", None), (None, None)]
)
def test_primary_language(etiqueta: str | None, esperado: str | None) -> None:
    assert primary_language(etiqueta) == esperado


def test_min_length_descarta_con_motivo() -> None:
    doc = _doc("")
    doc.text = "corto"

    assert MinLengthFilterStep(10).process(doc) == Discarded("texto_corto", "5 < 10 caracteres")
    doc.text = "suficientemente largo"
    assert MinLengthFilterStep(10).process(doc) is doc


def test_deduplicate_por_hash_de_texto() -> None:
    paso = DeduplicateStep()
    primero, segundo = _doc("", f"{B}/negocios"), _doc("", f"{B}/empresas/x")
    primero.text = segundo.text = "Mismo texto de una página soft-404."

    assert paso.process(primero) is primero
    assert paso.process(segundo) == Discarded("texto_duplicado", f"mismo texto que {B}/negocios")
    assert primero.content_hash == segundo.content_hash
    assert paso.reset().process(segundo) is segundo


# ---------------------------------------------------------------- cadena


class _Espia(CleaningStep):
    name = "espia"

    def __init__(self) -> None:
        super().__init__()
        self.llamado = False

    def process(self, doc: WorkingDocument) -> WorkingDocument | Discarded:
        self.llamado = True
        return doc


def test_cadena_se_corta_al_descartar() -> None:
    """Chain of Responsibility: tras un descarte, los eslabones siguientes no se ejecutan."""
    espia = _Espia()
    filtro = MinLengthFilterStep(100)
    filtro.set_next(espia)
    doc = _doc("")
    doc.text = "corto"

    resultado = filtro.handle(doc)

    assert isinstance(resultado, Discarded)
    assert not espia.llamado


def test_cadena_pasa_el_documento_al_siguiente() -> None:
    espia = _Espia()
    primero = NormalizeTextStep()
    primero.set_next(espia)

    primero.handle(_doc(""))

    assert espia.llamado
