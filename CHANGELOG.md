# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]

## [m02] - 2026-10-02 — Scraper (datos crudos)
### Añadido
- `BaseCrawler` (Template Method) y `SitemapBfsCrawler`: semillas de los dos índices de sitemap, BFS acotado, deduplicación por URL normalizada y por URL final, corte ante ráfagas de 403/429.
- Normalización de URLs (`utm_*`, fragmentos, barra final) y filtro de extensiones no HTML; semillas intercaladas por sección.
- Reintentos con backoff exponencial (`tenacity`) solo ante HTTP 500/502/503/504 y `httpx.TransportError` (timeouts y fallos de red).
- `RawStorage`: `data/raw/pages/<sha1>.html` + `manifest.jsonl` (con `lastmod`) con escritura atómica, fusión del manifest y re-ejecución incremental por huella del texto visible.
- Comando `scrape [--max-pages N]` y `crawl_report.json`.
- Configuración: `CRAWL_MAX_RETRIES`, `CRAWL_BACKOFF_SECONDS`, `CRAWL_BLOCK_THRESHOLD`, `RAW_DATA_DIR`. Dependencias: `tenacity`, `respx` (dev).
- ADR-010: la sala de prensa (`/acerca-de/sala-prensa/…`, que redirige a la portada de `prensa.bancolombia.com`) queda fuera del alcance.
- README: estado de M2, uso de `scrape`, patrón Template Method, limitaciones L-06 a L-08 y mejora futura (crawlear `prensa.bancolombia.com`).
- `docs/CONTEXTO_SESION.md` para retomar el proyecto en una sesión nueva.
- README inicial e incremental: estado M0–M14, nota de fuente Bancolombia (ADR-008), arquitectura, instalación verificada, patrones, stack, decisiones, limitaciones y mejoras futuras (publicado en `main` antes de M2).
### Cambiado
- `PoliteFetcher`, la lectura de robots y la de sitemaps se extraen de la exploración a módulos compartidos (`fetcher`, `discovery`, `urls`).
- La ayuda de la CLI dice Bancolombia.
- S-03 acotado: sin sala de prensa. `published_at` pasa a ser opcional en M3, solo si la página trae la fecha en metadatos.
- `docs/exploracion_sitio.md`: se explica que las 74 URLs de sala de prensa son 73 de `sitemap-sala-de-prensa.xml` más 1 que solo está en `sitemap-personas.xml`.
- Proceso: el README se actualiza en cada módulo (Definition of Done, punto 7; regla en `CLAUDE.md`). M14 pasa a ser pulido final y verificación desde cero.
- La tabla de limitaciones se mueve de `00_VISION_GENERAL.md §12` al README; §12 la enlaza.
### Corregido
- La detección incremental ya no usa el hash de bytes, que el marcado volátil del sitio hacía inútil.
- `elapsed_ms` registra el tiempo real también en redirecciones omitidas, fallos de red y exceso de redirecciones (antes 0).

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
