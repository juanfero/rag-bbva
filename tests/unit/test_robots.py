"""Pruebas del parser y la política de robots.txt (M1, sin red)."""

from pathlib import Path

import pytest

from rag_bbva.scraping.robots import RobotsRule, parse_robots

FIXTURES = Path(__file__).parent.parent / "fixtures" / "robots"
UA = "RAG-BBVA-TechTest/1.0"


@pytest.fixture(scope="module")
def bancolombia() -> str:
    """`robots.txt` real de www.bancolombia.com (copia del 2026-10-01)."""
    return (FIXTURES / "bancolombia.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("pattern", "path", "esperado"),
    [
        ("/personas/buscador", "/personas/buscador", True),
        ("/personas/buscador", "/personas/buscador/extra", True),
        ("/personas/buscador", "/personas", False),
        ("/*pdf*", "/documentos/tarifas.pdf", True),
        ("/*pdf*", "/personas/cuentas", False),
        ("/*.pdf$", "/doc.pdf", True),
        ("/*.pdf$", "/doc.pdf?v=2", False),
        ("/*?ofertaId*", "/personas/tarjetas?ofertaId=9", True),
        ("/connect/*.css", "/connect/tema/estilo.css", True),
    ],
)
def test_rule_matches_con_comodines(pattern: str, path: str, esperado: bool) -> None:
    """`*` equivale a cualquier secuencia y `$` ancla al final de la ruta."""
    assert RobotsRule(allow=False, pattern=pattern).matches(path) is esperado


def test_parse_bancolombia_sitemaps_y_grupos(bancolombia: str) -> None:
    """Se extrae el sitemap declarado y los grupos con sus User-Agents."""
    robots = parse_robots(bancolombia)

    assert robots.sitemaps == ("https://www.bancolombia.com/sitemap-index.xml",)
    entrenamiento = robots.groups[0]
    assert set(entrenamiento.user_agents) >= {"GPTBot", "ClaudeBot", "Google-Extended"}
    assert entrenamiento.rules == (RobotsRule(allow=False, pattern="/"),)


def test_bancolombia_nuestro_ua_cae_en_grupo_comodin(bancolombia: str) -> None:
    """Nuestro UA no está nombrado: aplica el grupo `*`, no el de bots de entrenamiento."""
    robots = parse_robots(bancolombia)

    grupos = robots.groups_for(UA)
    assert len(grupos) == 1
    assert "*" in grupos[0].user_agents
    assert "ClaudeBot" not in grupos[0].user_agents


@pytest.mark.parametrize(
    ("url", "permitido"),
    [
        ("https://www.bancolombia.com/personas", True),
        ("https://www.bancolombia.com/personas/cuentas/ahorros", True),
        ("https://www.bancolombia.com/personas/buscador", False),
        ("https://www.bancolombia.com/rest/api/x", False),
        ("https://www.bancolombia.com/personas/formulario-preaprobados", False),
        ("https://www.bancolombia.com/docs/reglamento.pdf", False),
        ("https://www.bancolombia.com/personas/solicitud-de-productos/tarjeta-visa-oro", False),
        ("https://www.bancolombia.com/connect/tema/estilo.css", True),
        ("https://www.bancolombia.com/robots.txt", True),
    ],
)
def test_bancolombia_is_allowed(bancolombia: str, url: str, permitido: bool) -> None:
    """La política aplicada a URLs reales coincide con lo esperado."""
    assert parse_robots(bancolombia).is_allowed(url, UA) is permitido


def test_bots_de_entrenamiento_bloqueados(bancolombia: str) -> None:
    """Los bots nombrados en el bloque de entrenamiento de IA no pueden entrar."""
    robots = parse_robots(bancolombia)

    assert not robots.is_allowed("https://www.bancolombia.com/personas", "GPTBot/1.1")
    assert not robots.is_allowed("https://www.bancolombia.com/personas", "ClaudeBot")


def test_regla_mas_larga_gana_y_allow_gana_empates() -> None:
    """La regla con patrón más largo decide; con igual longitud gana Allow."""
    robots = parse_robots(
        "User-agent: *\nDisallow: /privado\nAllow: /privado/publico\nDisallow: /x\nAllow: /x\n"
    )

    assert not robots.is_allowed("/privado/secreto", UA)
    assert robots.is_allowed("/privado/publico/a", UA)
    assert robots.is_allowed("/x", UA)


def test_grupo_especifico_tiene_prioridad_sobre_comodin() -> None:
    """Si un grupo nombra nuestro product token, se ignora el grupo `*`."""
    robots = parse_robots(
        "User-agent: *\nDisallow: /\n\nUser-agent: rag-bbva-techtest\nDisallow: /admin\n"
    )

    assert robots.is_allowed("/personas", UA)
    assert not robots.is_allowed("/admin/x", UA)
    assert not robots.is_allowed("/personas", "OtroBot/2.0")


def test_grupos_que_coinciden_se_combinan() -> None:
    """Dos grupos para el mismo agente suman sus reglas."""
    robots = parse_robots("User-agent: *\nDisallow: /a\n\nUser-agent: *\nDisallow: /b\n")

    assert not robots.is_allowed("/a", UA)
    assert not robots.is_allowed("/b", UA)


def test_disallow_vacio_comentarios_y_claves_raras() -> None:
    """Disallow vacío no restringe; comentarios, mayúsculas y basura se toleran."""
    robots = parse_robots(
        "# comentario\nUSER-AGENT: *   # fin de línea\nDisallow:\nALLOW: /blog/\n"
        "linea sin dos puntos\nClave-Desconocida: x\nSitemap: https://ej.co/s.xml\n"
    )

    assert robots.is_allowed("/cualquier/cosa", UA)
    assert robots.sitemaps == ("https://ej.co/s.xml",)
    assert robots.groups[0].rules == (RobotsRule(allow=True, pattern="/blog/"),)


def test_crawl_delay() -> None:
    """Se lee Crawl-delay; valores inválidos se ignoran."""
    robots = parse_robots("User-agent: *\nCrawl-delay: 2.5\n\nUser-agent: x\nCrawl-delay: abc\n")

    assert robots.crawl_delay(UA) == 2.5
    assert robots.crawl_delay("x") is None


def test_robots_vacio_permite_todo() -> None:
    """Sin reglas, todo está permitido."""
    robots = parse_robots("")

    assert robots.is_allowed("/lo-que-sea", UA)
    assert robots.sitemaps == ()
    assert robots.crawl_delay(UA) is None
