# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]

## [m01] - 2026-10-01 — Exploración del sitio
### Añadido
- Parser y política de `robots.txt` según RFC 9309, con comodines `*`/`$` (`scraping/robots.py`).
- Parser de sitemaps (índice/urlset, gzip, protección XXE) y conteo por sección (`scraping/sitemap.py`).
- Análisis de HTML estático y comparación con el renderizado (`scraping/page_analysis.py`).
- `PoliteFetcher` y `SiteExplorer` (`scraping/exploration.py`); script `scripts/explore_site.py`.
- `docs/exploracion_sitio.md`, evidencia JSON de la corrida y bitácora `docs/modulos/M01.md`.
- Variable `CRAWL_TIMEOUT_SECONDS`; dependencias `httpx`, `beautifulsoup4` y `lxml`.
- ADR-008 (fuente: Bancolombia) y ADR-009 (httpx sin Playwright).
- Regla en `CLAUDE.md`: los textos visibles al usuario dicen Bancolombia.
- Tabla de limitaciones conocidas para el README (`00_VISION_GENERAL.md §12`).
### Cambiado
- Fuente de datos: `www.bancolombia.com` en lugar de `www.bbva.com.co`, que bloquea crawlers con 403. Defaults `TARGET_BASE_URL` y `QDRANT_COLLECTION=bancolombia_docs`.
- Defaults del crawl: `CRAWL_MAX_PAGES=1200` y `CRAWL_MAX_DEPTH=1` (S-04).
- Supuestos S-02, S-03 y S-04 confirmados; plan de M2, M3, M12 y M14 actualizado con las decisiones del checkpoint.
### Corregido
- El fetcher ya no sigue redirecciones fuera del dominio ni hacia rutas prohibidas por `robots.txt`.

## [m00] - 2026-10-01 — Fundaciones
### Añadido
- Estructura del paquete `rag_bbva` (layout `src/`) y de `tests/`, `eval/` según la visión general §5.
- `pyproject.toml` con dependencias de M0 y configuración de `ruff` y `pytest` (marcadores `integration`, `slow`).
- `.gitignore` (`.env`, `data/`, modelos, cachés) y `.dockerignore`.
- Configuración externalizada (`config.py`) con todas las variables de §7, validación de rangos y reglas cruzadas; `.env.example` documentado.
- Jerarquía de excepciones con base `RagBbvaError`.
- Logging estructurado en JSON con nivel desde `LOG_LEVEL`.
- CLI con Typer y comando `version`.
- `Dockerfile` base (python:3.11-slim, usuario no root) y `docker-compose.yml` mínimo.
- ADR-006 (clave de xAI opcional en `Settings`) y ADR-007 (dependencias incrementales).
- Bitácora `docs/modulos/M00.md`.

## [Documentación inicial]
### Añadido
- Documentación inicial: visión general, plan de módulos, registro de decisiones y plantilla de bitácora.
### Cambiado
- LLM: Grok (xAI) en lugar de Ollama local (ADR-003); se elimina el servicio `ollama` del compose.
- Se confirman Streamlit como UI y Linux como entorno; M13 entra en alcance.
