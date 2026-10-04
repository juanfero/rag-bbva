# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]

## [m10] - en revisión — Interfaz conversacional
### Añadido
- Interfaz Streamlit (`src/rag_bbva/ui/app.py`) que consume la API por HTTP con `ApiClient` (sin importar el núcleo; lo verifica una prueba):
  - chat con indicador de espera;
  - citas [n] como enlaces y fuentes en un desplegable;
  - aviso propio para "sin información suficiente";
  - 👍/👎 que se deshabilitan tras votar;
  - barra lateral: nueva conversación, `conversation_id`, retomar por lista o por ID y estado de `/health`;
  - modo detalle, errores 404/422/503 amigables y aviso de prototipo.
- `ApiClient` y `ApiClientError` (código HTTP + mensaje para el usuario); `/health` con 503 se trata como reporte degradado.
- Comandos `ui` (Streamlit con `--host`, `--port`, `--api-url`) y `chat` (CLI de respaldo sobre `RAGService`, con `--conversation-id`).
- Configuración: `API_BASE_URL`, `UI_HOST`, `UI_PORT`, `UI_REQUEST_TIMEOUT_SECONDS`. Dependencia: `streamlit`.
- `scripts/ui_guion.py`: guion de la interfaz contra la API real con `AppTest`.
- README: sección "Uso de la interfaz conversacional" con capturas, Streamlit ✅, L-15.
- Ajustes derivados de M9:
  - respaldo del LLM también ante timeout y 5xx tras un reintento (`LLMUnavailableError`);
  - tope de reloj por llamada (`LLM_TIMEOUT_SECONDS=20`);
  - presupuesto por turno (`LLM_TURN_BUDGET_SECONDS=45`), con 503 "el servicio está lento" sin guardar el turno.
  - ADR-017 ampliada.
### Corregido
- La marca `[SIN_INFO]` solo cuenta como abstención al inicio de la respuesta: una respuesta parcial con citas ya no queda como `no_answer`.
- `eval/calibration.jsonl`: n10 y n13 reetiquetadas como respondibles tras verificar literalmente sus datos en el sitio (campo `note`). M6 25/30, M9 29/30 con los mismos umbrales.
### Cambiado
- `UI_REQUEST_TIMEOUT_SECONDS` por defecto 60 s (antes 180).
- L-06 (las noticias de `/acerca-de` se pueden citar, sin fecha), L-11 y L-15.

## [m09] - 2026-10-03 — Servicio RAG + API
### Añadido
- `RAGService` (Facade): historial (`HISTORY_WINDOW_N`) → reformulación → recuperación → reranking → umbral → generación → guardado; respuesta con `conversation_id`, `message_id`, `answer`, `sources[{n, url, title}]`, `no_answer`, `rewritten_query`, `timings` por etapa y `tokens`.
- Turno atómico (ADR-014): `ConversationRepository.add_turn` guarda pregunta, respuesta y, si hace falta, la conversación nueva en una sola transacción.
- API FastAPI con `create_app` e inyección de dependencias (`Depends`): `POST /chat`, `GET /conversations`, `GET /conversations/{id}/messages`, `POST /messages/{id}/feedback`, `GET /health` (sin gastar tokens). Lifespan con `warm_up` de embedder y reranker.
- Errores JSON `{error, detail}` sin trazas: 422, 404, 503 (LLM, cupo, Qdrant, historial, configuración) y 500.
- `HealthChecker` (Qdrant, SQLite, configuración del LLM) y `UnconfiguredLLMProvider` (la API arranca sin clave y lo informa).
- Comando `serve` (uvicorn) y configuración `API_HOST`, `API_PORT`, `CHAT_QUESTION_MAX_CHARS`. Dependencias: `fastapi`, `uvicorn`.
- README: sección API con ejemplos `curl`, `/docs`, Facade e inyección de dependencias ✅, L-14.
- Umbral doble (ADR-016): `RERANK_HARD_MIN_SCORE=-3.0`, zona gris con LLM y marca de abstención `[SIN_INFO]` → `no_answer`; `gray_zone` en la respuesta de `/chat`; `scripts/zona_gris_evidence.py`.
- `FallbackLLMProvider` (Decorator, ADR-017) con `LLM_FALLBACK_MODEL=gemini-3.1-flash-lite` solo ante 429 (`LLMQuotaError`); `/health` informa el modelo de respaldo.
### Cambiado
- Prompt de respuesta: en los seguimientos agrega la pregunta autónoma (ADR-015) y pide la marca `[SIN_INFO]` al abstenerse (ADR-016); `PROMPT_VERSION` 2026-10-03.2.
- L-11: el respaldo de modelo pasa de manual a automático.
- SQLite seguro entre hilos (`check_same_thread=False`, espera de 15 s, WAL) y valoración solo de respuestas del asistente.
### Corregido
- Plan de M12: la clave que entra por `.env` es `GEMINI_API_KEY` (quedaba `XAI_API_KEY`).
- Una colección inexistente en Qdrant (servidor o `:memory:`) da un `IndexingError` claro que indica ejecutar `ingest`.

## [m08] - 2026-10-03 — Memoria conversacional
### Añadido
- `ConversationRepository` (Repository) con `SqlAlchemyConversationRepository` (SQLite en `HISTORY_DB_PATH`) e `InMemoryConversationRepository`.
- Tablas `conversations` y `messages` del plan, con fuentes, latencias, score, `no_answer`, tokens y valoración por mensaje.
- `get_last_n` en orden cronológico (por orden de inserción); `set_feedback`; título = primera pregunta.
- `ConversationNotFoundError` y `MessageNotFoundError` (subclases de `HistoryError`).
- `ComponentFactory.create_conversation_repository()` y comando `history` (solo lectura).
- ADR-013: un `conversation_id` desconocido se informa, no se crea. S-05 actualizado.
- README: memoria conversacional, Repository ✅, L-13. Dependencia: `sqlalchemy` 2.x.

## [m07] - 2026-10-03 — Generación con LLM
### Añadido
- `LLMProvider` (Strategy):
  - `OpenAICompatibleProvider` con dos variantes: `GeminiProvider` (por defecto, ADR-012) y `XaiGrokProvider`; más `FakeLLMProvider`;
  - SDK `openai` con reintentos propios (tenacity, el SDK con `max_retries=0`) solo ante 429, 5xx y timeouts;
  - streaming, registro de tokens y errores amigables (clave inválida, sin créditos, cupo).
- Comando `llm-check`, que lista los modelos y confirma `LLM_MODEL`.
- Prompts versionados (`llm/prompts.py`, versión `2026-10-02.1`):
  - solo contexto, citas [n], no inventar cifras, aclarar si preguntan por otra entidad;
  - contexto delimitado contra inyección;
  - prompt de reformulación.
- `QueryRewriter` (`QUERY_REWRITE_MODE=off|history_only|always`, por defecto `history_only` según el experimento).
- `AnswerGenerator`: sin llamar al LLM si el umbral marca `no_answer`; con streaming.
- Post-proceso de citas: [n] → URL, deduplicadas, sin índices inválidos.
- Configuración: `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `LLM_REASONING_EFFORT`, `LLM_MAX_RETRIES`, `LLM_BACKOFF_SECONDS`, `LLM_PRICE_*`, `QUERY_REWRITE_*`. Dependencia: `openai` 3.x.
- `scripts/llm_evidence.py` (preguntas de punta a punta y experimento de reformulación) y prueba de integración contra el LLM real.
- ADR-012: Gemini 2.5 Flash con clave gratuita; Grok queda como alternativa.
- README: generación, `llm-check`, configuración de la clave, Strategy del LLM, costo, L-11 (cupo diario) y L-12 (datos en el nivel gratuito).
- `tests/unit/test_secrets.py`: falla si algún archivo del repo contiene una clave de API o si `.env` deja de estar ignorado.
- El 429 de cupo diario no se reintenta y explica cómo seguir (clave de un proyecto nuevo, solo en `.env`).
### Cambiado
- `LLM_PROVIDER` por defecto `gemini` y `LLM_MODEL` `gemini-2.5-flash` (antes `xai` / `grok-4.7`). ADR-003 queda reemplazada como opción por defecto.

## [m06] - 2026-10-02 — Recuperación + reranker
### Añadido
- `Retriever`: consulta con `query: `, top-k del `VectorStore` con filtro por sección, reranking, diversidad y marca `no_answer`. `RetrievalResult` con candidatos, scores y tiempos (`retrieval_ms`, `rerank_ms`). Incluye `warm_up()`.
- `Reranker` (Strategy): `CrossEncoderReranker` (mmarco-mMiniLMv2, CPU, `max_length` 512) y `NoOpReranker`; `select_diverse` (como mucho `RERANK_MAX_CHUNKS_PER_DOC` chunks por página).
- Fábrica: `create_reranker` (según `RERANKER_ENABLED`) y `create_retriever`.
- Calibración del umbral: `eval/calibration.jsonl` (15 respondibles + 15 no respondibles), `retrieval/calibration.py` y `scripts/calibrate_reranker.py`. `RERANK_MIN_SCORE=1.6` (27/30 aciertos dentro de la muestra de calibración; M13 lo valida aparte).
- Comando `search "pregunta" [--no-rerank] [--section X] [--top-n N]`.
- Configuración: `RERANK_MAX_CHUNKS_PER_DOC`, `RERANK_MIN_SCORE`, `RERANKER_MAX_LENGTH`, `RERANKER_BATCH_SIZE`.
- README: búsqueda, bonus del reranker, umbral y su justificación, patrón Strategy del reranker.

## [m05] - 2026-10-02 — Indexación vectorial (Qdrant)
### Añadido
- Servicio `qdrant` en `docker-compose.yml`: imagen `qdrant/qdrant:v1.19.1`, volumen `qdrant_data`, puerto solo en `127.0.0.1` y healthcheck sobre `/readyz`. Dependencia `qdrant-client` 1.19.x.
- `VectorStore` → `QdrantVectorStore` (Adapter): colección coseno, upsert por lotes con payload, búsqueda top-k con filtro por `section` (índice de payload), count, borrado por ids y errores traducidos a `IndexingError`.
- Comando `ingest [--recreate]`:
  - ids `uuid5` del `chunk_id`;
  - sincronización completa: nuevos, actualizados, sin cambios y eliminados por hash del texto embebido;
  - caché de embeddings en `data/embeddings/`;
  - reporte con tiempos por etapa.
- Configuración: `QDRANT_TIMEOUT_SECONDS`, `QDRANT_BATCH_SIZE`, `EMBEDDINGS_CACHE_DIR`; `QDRANT_URL=":memory:"` para el modo local.
- Tests con `QdrantClient(":memory:")` y prueba de integración contra el Qdrant real (se salta si no responde).
- README: cómo levantar Qdrant, comando `ingest`, patrón Adapter, stack.
### Cambiado
- `QDRANT_URL` por defecto `http://localhost:6333` (el Qdrant del compose desde la máquina local).

## [m04] - 2026-10-02 — Chunking + embeddings
### Añadido
- Estrategias de chunking (Strategy):
  - `HeadingAwareChunker`: secciones markdown agrupadas hasta `CHUNK_SIZE` y partidas por tamaño solo si no caben, sin cortar palabras, con `heading_path` y encabezado de contexto en el texto a embeber;
  - `FixedSizeChunker` como línea base.
- `Embedder` → `SentenceTransformerEmbedder` (e5-small en CPU, prefijos `query:`/`passage:`, L2, lotes, carga perezosa) y `FakeEmbedder` determinista.
- `ComponentFactory` (Factory) para chunker y embedder.
- `data/chunks/chunks.jsonl` + `chunk_report.json`, con verificación del máximo de tokens según el tokenizer real. Comando `chunk [--strategy]`.
- Configuración: `CHUNKS_DATA_DIR`, `CHUNKING_STRATEGY`, `CHUNK_MIN_CHARS`, `EMBEDDING_PROVIDER`, `EMBEDDING_BATCH_SIZE`, `MODEL_CACHE_DIR`.
- Dependencias: `sentence-transformers`, `torch` CPU-only (instalado desde el índice CPU de PyTorch) y `numpy`.
- Fixture del glosario limpio real (116 021 caracteres).
- README: instalación con torch CPU, comando `chunk`, patrones Strategy y Factory, stack.
- Tests `slow` del modelo real que se saltan con motivo si el modelo no está en la caché local; carga sin consultar el Hub cuando ya está en caché (`local_files_only`).
### Cambiado
- Limpieza (ajuste a M3 derivado de la evidencia de recuperación):
  - quita los bloques repetidos de venta cruzada ("Descubre otros canales que te van a interesar", "Si te gustó este producto, estos te van a interesar") y el rótulo "Link copiado en porta papeles";
  - el chequeo de fugas los detecta (`venta_cruzada`, `interfaz`).
- Limpieza: trafilatura solo se usa si además conserva el orden de los bloques del contenedor.

## [m03] - 2026-10-02 — Limpieza (datos limpios)
### Añadido
- Pipeline de limpieza con el patrón Chain of Responsibility (`processing/steps.py`, `processing/pipeline.py`):
  - parseo y metadatos: título, `html_lang`, `published_at` solo de metadatos, migas y plantilla;
  - eliminación de boilerplate: navegación, pie, menús de portlet con `${…}`, `.lrpError`, visor de WCM sin configurar, iconos, cookies y bloque rotativo de relacionados;
  - extracción con trafilatura y *fallback* por selector según la cobertura de vocabulario;
  - normalización, detección de idioma, longitud mínima y deduplicación por hash de texto.
- Salida `data/clean/documents.jsonl` + `clean_report.json` determinista, con descartes por motivo, distribución de longitudes, conteos por sección, plantilla, extracción e idioma, respaldo de idioma por plantilla y chequeo de fugas de boilerplate (`processing/quality.py`).
- Comando `clean`. Configuración: `CLEAN_DATA_DIR`, `CLEAN_MIN_CHARS`, `CLEAN_MIN_EXTRACTION_COVERAGE`. Dependencia: `trafilatura`.
- Exclusión configurable por prefijo de ruta antes de pedir la URL (`CRAWL_EXCLUDE_PATH_PREFIXES`, por defecto la sala de prensa; outcome `excluida`), derivada de ADR-010.
- Fixtures HTML reales recortados de las 3 plantillas y `scripts/trim_html_fixture.py`.
- README: estado de M3, comando `clean`, patrón Chain of Responsibility, L-08 resuelta, L-09 y L-10.
### Cambiado
- El manifest se escribe de forma incremental (una línea por URL, con flush) y se compacta al final.
- Ctrl+C y SIGTERM cierran el crawl con `abort_reason: "interrumpido"` (código de salida 130).
- `RawStorage` expone `read_manifest()` de solo lectura para la limpieza.
### Corregido
- Un crawl interrumpido ya no deja HTML sin su línea en el manifest: el corte en la página 500 había dejado 308.
- ADR-010: "en 4 URLs" en lugar de "otras 4".
### Decisiones
- ADR-011: el alcance incluye cualquier ruta del dominio alcanzada por enlace o redirección (S-03 ampliado).

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
