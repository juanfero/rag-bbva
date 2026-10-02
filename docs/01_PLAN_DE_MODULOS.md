# Plan de módulos

> Regla de oro: **no se inicia el módulo siguiente hasta que el actual pase todas sus pruebas y esté documentado.**
> Cada módulo = 1 rama + varios commits pequeños + bitácora en `docs/modulos/MXX.md` + tag `mXX`.

## Resumen

| # | Módulo | Entregable principal | Requisito que cubre | Est. |
|---|---|---|---|---|
| M0 | Fundaciones | Repo, estructura, config, logging, excepciones, Docker base | R1, R2, R3, B2, B3 | 2 h |
| M1 | Exploración del sitio | `docs/exploracion_sitio.md` + script de diagnóstico | F1 (decisiones), S-03 | 1.5 h |
| M2 | Scraper (datos crudos) | Crawler + `data/raw/` | F1, F2 | 3 h |
| M3 | Limpieza (datos limpios) | Pipeline de limpieza + `data/clean/` | F2 | 2.5 h |
| M4 | Chunking + embeddings | Estrategias de chunking y embedder | F3 | 2 h |
| M5 | Indexación vectorial | Qdrant + comando `ingest` idempotente | F3 | 2 h |
| M6 | Recuperación + reranker | Retriever + cross-encoder | F3, B1 | 2 h |
| M7 | Generación con LLM | Proveedor Grok (xAI) + prompts con citas | F4 (núcleo) | 2.5 h |
| M8 | Memoria conversacional | Repositorio SQLite + ventana N | F5, R5 | 2 h |
| M9 | Servicio RAG + API | Fachada `RAGService` + FastAPI | F4 | 2.5 h |
| M10 | Interfaz conversacional | Streamlit (chat) + CLI | F4, R6 | 2 h |
| M11 | Analítica del historial | Métricas + valores de impacto | R8 | 2.5 h |
| M12 | Dockerización completa | `docker compose up` de un solo comando | R2 | 2.5 h |
| M13 | Evaluación de calidad | Golden set + métricas de retrieval con/sin reranker | Valor agregado | 2 h |
| M14 | README final y cierre | README completo, verificación desde cero | R4, R9 | 2 h |

**Total estimado:** ~33 h. Sin fecha límite fija: todos los módulos están en alcance.

---

## Definition of Done (aplica a todo módulo)

1. ✅ `pytest` en verde (tests del módulo **y** de los módulos anteriores — sin regresiones).
2. ✅ `ruff check .` sin errores.
3. ✅ Bitácora `docs/modulos/MXX.md` completa (plantilla en `docs/modulos/_PLANTILLA.md`).
4. ✅ `CHANGELOG.md` actualizado.
5. ✅ Decisiones nuevas registradas en `docs/02_DECISIONES.md`.
6. ✅ Commits descriptivos (Conventional Commits), merge a `main`, tag `mXX`, push.

---

## M0 — Fundaciones

**Objetivo:** dejar el esqueleto que todos los módulos reutilizan.

**Tareas**
- `git init`, `.gitignore` (data/, .env, modelos, caches), `pyproject.toml` con dependencias y config de `ruff`/`pytest`.
- Estructura de carpetas de `00_VISION_GENERAL.md §5`.
- `config.py`: clase `Settings` (pydantic-settings) con **todas** las variables de §7 y `get_settings()` cacheada.
- `.env.example` documentado (incluye `XAI_API_KEY=` vacío). `.env` en `.gitignore` desde el primer commit.
- `exceptions.py`: `RagBbvaError` → `ScrapingError`, `ProcessingError`, `IndexingError`, `RetrievalError`, `LLMError`, `HistoryError`.
- `logging_conf.py`: logging estructurado (nivel desde `.env`).
- `cli.py` con Typer y comando `version`.
- `Dockerfile` base (python:3.11-slim) y `docker-compose.yml` mínimo (solo `api` con comando placeholder).
- `CLAUDE.md`, `CHANGELOG.md`, carpeta `docs/` con estos documentos.

**Pruebas de aceptación**
- `test_settings_defaults`: carga defaults sin `.env`.
- `test_settings_env_override`: una variable de entorno sobrescribe el default (p. ej. `HISTORY_WINDOW_N=3`).
- `test_settings_validation`: valores inválidos (`CHUNK_OVERLAP >= CHUNK_SIZE`, `HISTORY_WINDOW_N < 0`) lanzan error.
- `test_api_key_is_secret`: `XAI_API_KEY` es `SecretStr` y no aparece en `repr` ni en logs.
- `test_exceptions_hierarchy`: todas heredan de `RagBbvaError`.
- `test_cli_version`: el comando responde.
- Manual: `docker build .` termina sin errores.

**Commits sugeridos:** `chore: inicializa repositorio y estructura` · `feat(config): agrega settings externalizados con pydantic-settings` · `feat(core): agrega jerarquía de excepciones y logging` · `build: agrega Dockerfile y docker-compose base` · `docs: agrega visión general y plan de módulos`

---

## M1 — Exploración del sitio

**Objetivo:** tomar decisiones de scraping con evidencia, no a ciegas.

**Tareas**
- Script `scripts/explore_site.py` que:
  - Lee y resume `robots.txt` (rutas permitidas/prohibidas, sitemaps declarados).
  - Lee el/los `sitemap.xml` y cuenta URLs por sección (`/personas/`, `/empresas/`, …).
  - Descarga una muestra (~10 páginas de secciones distintas) y compara texto del HTML estático vs. lo visible → **¿hace falta renderizar JS?**
  - Registra tipos de contenido (HTML, PDF), códigos de respuesta y tamaños.
- Documento `docs/exploracion_sitio.md` con hallazgos y **decisiones**: secciones incluidas/excluidas, límite de páginas, herramienta (httpx vs. Playwright), selectores de contenido principal.

**Pruebas de aceptación**
- Tests unitarios del parser de `robots.txt` y de sitemap con fixtures locales (sin red).
- El documento de exploración existe y responde: alcance, herramienta, riesgos.
- Checkpoint con Juan Felipe: **validar el alcance antes de M2**.

---

## M2 — Scraper (datos crudos)

**Objetivo:** descargar el sitio de forma respetuosa y reproducible.

**Diseño**
- `BaseCrawler` (**Template Method**): `discover_urls() → fetch() → validate() → persist()`.
- `SitemapBfsCrawler`: semillas desde sitemap + BFS acotado por dominio, profundidad y `CRAWL_MAX_PAGES`.
- `RobotsPolicy`: verificación por URL. Normalización de URLs (quitar fragmentos, parámetros de tracking, barras finales).
- Rate limiting (`CRAWL_DELAY_SECONDS`), reintentos con backoff exponencial (`tenacity`) solo en errores transitorios (5xx, timeouts), timeouts configurables.
- Almacenamiento crudo: `data/raw/pages/<sha1(url)>.html` + `data/raw/manifest.jsonl` (url, status, content-type, fecha, hash del contenido, profundidad, ruta del archivo).
- Re-ejecución incremental: si el hash no cambió, no se reescribe.
- CLI: `python -m rag_bbva.cli scrape [--max-pages N]`.

**Pruebas de aceptación** (con `respx`, sin red real)
- Respeta `robots.txt` (URL prohibida no se descarga).
- No sale del dominio objetivo; no repite URLs normalizadas iguales.
- Respeta `max_pages` y `max_depth`.
- Reintenta en 503 y se rinde tras N intentos registrando el error **sin romper el crawl**.
- 404 se registra en el manifest y no se guarda HTML.
- Manifest y archivos generados con el esquema esperado.
- Manual: crawl real limitado (p. ej. 30 páginas) y revisión de una muestra.

---

## M3 — Limpieza (datos limpios)

**Objetivo:** convertir HTML crudo en documentos de texto limpios y con metadatos.

**Diseño**
- `CleaningPipeline` (**Chain of Responsibility / Pipeline**) con pasos: extraer contenido principal (trafilatura + fallback BeautifulSoup) → eliminar boilerplate (menú, footer, banners de cookies) → normalizar Unicode y espacios → conservar estructura (títulos `#`, listas, tablas simples) → filtrar documentos vacíos/cortos → deduplicar por hash de texto.
- Documento limpio (`data/clean/documents.jsonl`): `doc_id, url, title, section, breadcrumbs, text, lang, scraped_at, content_hash, n_chars`.
- Reporte de calidad: documentos procesados, descartados (y motivo), longitud media.
- CLI: `clean`.

**Pruebas de aceptación**
- Cada paso probado aislado con fixtures HTML reales guardados en `tests/fixtures/`.
- Se elimina el menú/footer de una página fixture; el contenido principal se conserva.
- Duplicados exactos se eliminan; documentos < umbral se descartan con motivo.
- La sección se deriva correctamente de la URL.
- El pipeline es determinista (misma entrada → misma salida).

---

## M4 — Chunking + embeddings

**Diseño**
- `ChunkingStrategy` (**Strategy**): `HeadingAwareChunker` (divide por títulos y luego por tamaño con solapamiento) y `FixedSizeChunker` (baseline). Cada chunk hereda metadatos (url, title, section) + `chunk_id` determinista.
- `Embedder` (interfaz) → `SentenceTransformerEmbedder` con prefijos `query:` / `passage:` (requeridos por e5), normalización L2, batching.
- `ComponentFactory` (**Factory**): crea chunker y embedder desde `Settings`.

**Pruebas de aceptación**
- Ningún chunk supera `CHUNK_SIZE`; el solapamiento es el configurado; no hay chunks vacíos.
- `chunk_id` estable entre ejecuciones.
- Embedder devuelve vectores de la dimensión esperada y normalizados (test con modelo real marcado `@pytest.mark.slow` + test con embedder falso para el resto).
- Similitud: una consulta sobre "tarjeta de crédito" queda más cerca de un texto de tarjetas que de uno de CDT.

---

## M5 — Indexación vectorial

**Diseño**
- `VectorStore` (interfaz) → `QdrantVectorStore`: crear colección (distancia coseno), upsert por lotes con payload (texto + metadatos), búsqueda con filtros opcionales por sección.
- Comando `ingest`: clean → chunks → embeddings → upsert. **Idempotente** (IDs deterministas: re-ingestar no duplica). Opción `--recreate`.

**Pruebas de aceptación**
- Con Qdrant en modo `:memory:`: crear colección, upsert, búsqueda devuelve el chunk esperado en top-1.
- Re-ingestar no aumenta el número de puntos.
- Filtro por sección funciona.
- Error de conexión a Qdrant → `IndexingError` con mensaje claro.

---

## M6 — Recuperación + reranker (BONUS)

**Diseño**
- `Retriever`: embed de la consulta → top-k (`RETRIEVAL_TOP_K`) desde el vector store.
- `Reranker` (**Strategy**): `CrossEncoderReranker` y `NoOpReranker` (cuando `RERANKER_ENABLED=false`). Devuelve top-n (`RERANK_TOP_N`) con score.
- Umbral mínimo de relevancia para marcar "sin información suficiente".

**Pruebas de aceptación**
- El reranker reordena: en un caso fixture, el documento correcto sube de posición respecto al retrieval puro.
- `NoOpReranker` preserva orden y corta a top-n.
- Con el flag desactivado, la fábrica entrega `NoOpReranker`.
- Se registran latencias de retrieval y reranking.

---

## M7 — Generación con LLM

**Diseño**
- `LLMProvider` (**Strategy**) → `XaiGrokProvider` (SDK `openai` con `base_url=XAI_BASE_URL`) y `FakeLLMProvider` para tests. Timeout, reintentos con backoff en 429/5xx, streaming y registro de tokens (prompt/completion) para la analítica de costo.
- Comando `llm-check`: llama `GET /v1/models` y confirma que `LLM_MODEL` existe para la clave configurada.
- Plantillas de prompt en español: sistema restrictivo ("responde solo con el contexto; si no está, dilo"), contexto numerado `[1]…[n]`, citas obligatorias, tono profesional.
- Prompt de **reformulación** de la pregunta usando el historial (para preguntas como "¿y cuál es su tasa?").
- Post-procesamiento: mapear citas `[n]` a URLs.

**Pruebas de aceptación**
- Plantilla renderiza contexto e historial correctamente (snapshot test).
- Con `FakeLLMProvider`: las citas se mapean a las URLs correctas.
- API caída / timeout / 429 → `LLMError` y mensaje amigable, sin traza al usuario (simulado con `respx`).
- `XAI_API_KEY` ausente → error claro al crear el proveedor, no a mitad de una conversación.
- Test de integración (`@pytest.mark.integration`, requiere clave real) contra Grok: responde en español y con al menos una cita.

---

## M8 — Memoria conversacional

**Diseño**
- `ConversationRepository` (**Repository**): interfaz + `SqlAlchemyConversationRepository` (SQLite) + `InMemoryConversationRepository` (tests).
- Tablas: `conversations(id, created_at, updated_at, title)` y `messages(id, conversation_id, role, content, created_at, sources_json, retrieval_ms, rerank_ms, llm_ms, total_ms, top_score, no_answer, prompt_tokens, completion_tokens, feedback)`.
- `get_last_n(conversation_id, n=HISTORY_WINDOW_N)` en orden cronológico.

**Pruebas de aceptación**
- Se persiste y se recupera tras "reiniciar" (nueva instancia del repo sobre el mismo archivo).
- `get_last_n` respeta N y el orden; N=0 devuelve vacío.
- Conversaciones distintas no se mezclan.
- `conversation_id` inexistente → se crea o se informa según contrato documentado.

---

## M9 — Servicio RAG + API

**Diseño**
- `RAGService` (**Facade**): `ask(conversation_id, question)` → historial N → reformulación → retrieve → rerank → generate → persistir métricas → respuesta `{answer, sources, conversation_id, timings}`.
- FastAPI:
  - `POST /chat` · `GET /conversations` · `GET /conversations/{id}/messages` · `POST /messages/{id}/feedback`
  - `GET /health` (estado de Qdrant, SQLite y configuración del LLM, sin gastar tokens) · `GET /analytics/summary` (se completa en M11)
- Manejadores de excepciones globales → JSON `{error, detail}` con códigos HTTP coherentes.

**Pruebas de aceptación**
- `TestClient` + componentes falsos: flujo completo de `/chat` y persistencia.
- **Prueba de memoria:** segunda pregunta dependiente ("¿y los requisitos?") usa el historial (se verifica el prompt de reformulación recibido por el fake LLM).
- Validación de entrada (pregunta vacía → 422).
- `/health` refleja un servicio caído.

---

## M10 — Interfaz conversacional

**Diseño**
- Streamlit: chat con `st.chat_message`, selector "nueva conversación / continuar por ID", fuentes desplegables con links, botones 👍/👎, indicador de latencia. Consume la API (no importa el core directamente).
- CLI de respaldo: `python -m rag_bbva.cli chat --conversation-id X`.

**Pruebas de aceptación**
- Cliente HTTP de la UI probado con `respx`.
- Test de la CLI con `typer.testing.CliRunner` y servicio falso.
- Manual: guion de prueba (3 conversaciones, una retomada por ID tras reiniciar el contenedor).

---

## M11 — Analítica del historial

**Diseño**
- `analytics/metrics.py`: carga mensajes desde el repositorio → pandas → métricas de `00_VISION_GENERAL.md §8`.
- Agrupación de preguntas frecuentes (embeddings + clustering simple o normalización de texto).
- Salidas: CLI `metrics [--export csv]`, endpoint `/analytics/summary`, página "Métricas" en Streamlit.
- Script `scripts/seed_conversations.py` para generar historial de demo.

**Pruebas de aceptación**
- Con un historial fixture conocido, cada métrica da el valor calculado a mano.
- Historial vacío → métricas en cero sin errores.
- Export CSV con columnas esperadas.

---

## M12 — Dockerización completa

**Tareas**
- Dockerfile multi-stage, `torch` CPU-only, usuario no root, pre-descarga de modelos de embeddings/reranker en build (o caché en volumen).
- `docker-compose.yml`: `qdrant`, `init` (ingesta si la colección está vacía), `api`, `ui`; healthchecks, `depends_on: condition`, volúmenes, `env_file` (la `XAI_API_KEY` entra solo por `.env`).
- Snapshot opcional de `data/clean/` en el repo para que la demo no dependa del scraping en vivo (*a decidir*).

**Pruebas de aceptación**
- En una máquina Linux limpia: `cp .env.example .env` (+ `XAI_API_KEY`) y `docker compose up -d --build` → UI accesible y responde una pregunta con fuentes.
- `docker compose down && docker compose up -d` → el historial persiste.
- Smoke test automatizado `scripts/smoke_test.sh` contra `/health` y `/chat`.

---

## M13 — Evaluación de calidad

- `eval/golden_set.jsonl`: ~25 preguntas con URL esperada.
- Métricas: Hit@k y MRR **con y sin reranker** → tabla en el README (evidencia del bonus).

---

## M14 — README final y cierre

- README con las 7 secciones exigidas + diagrama de arquitectura + tabla de patrones con rutas de archivo + limitaciones honestas + mejoras futuras.
- Verificación: clonar en carpeta nueva y seguir el README literalmente.
- Revisión del historial de commits; tag `v1.0.0`.
