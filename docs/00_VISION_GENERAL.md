# Visión general del proyecto — Asistente RAG sobre el sitio de BBVA Colombia

> Documento maestro del proyecto. Todo módulo, decisión y prueba debe ser trazable a este documento.
> Estado: **v0.3 — decisiones confirmadas** (LLM: Gemini 2.5 Flash, ADR-012 · UI: Streamlit · Entorno: Linux · Fuente: Bancolombia, ADR-008).

---

## 1. Contexto del caso

| Campo | Valor |
|---|---|
| Rol evaluado | Machine Learning Engineer / AI Engineer |
| Modalidad | Individual, entrega en repositorio público con historial de commits |
| Tiempo estimado | 2 – 3 días |
| Fecha de entrega | Sin fecha límite fija (ver S-01) |
| Entorno de desarrollo | Linux + Docker |
| Cliente ficticio | BBVA Colombia |
| Fuente de datos | ~~https://www.bbva.com.co/~~ → **https://www.bancolombia.com/** (el caso permite otro banco; bbva.com.co bloquea a todo cliente no navegador con 403, ver ADR-008 y S-02) |

**Problema de negocio:** usuarios internos de BBVA Colombia necesitan consultar la información publicada en el sitio institucional sin hacer búsquedas manuales. Se requiere un asistente conversacional que responda **solo con base en el contenido del sitio**, citando las fuentes.

---

## 2. Requisitos del caso (lectura literal) y cómo se cubren

### 2.1 Funcionalidades obligatorias

| ID | Requisito (PDF) | Cómo se cumple | Módulo |
|---|---|---|---|
| F1 | Extraer información del sitio mediante web scraping | Crawler propio (sitemap + recorrido BFS acotado al dominio), respeta `robots.txt`, rate limiting y reintentos | M1, M2 |
| F2 | Almacenar datos scrapeados en local, **crudos y limpios** | `data/raw/` (HTML original + metadatos) y `data/clean/` (texto normalizado en JSONL) | M2, M3 |
| F3 | Vectorizar e indexar en una base de datos vectorial a elección | Embeddings open source + Qdrant self-hosted | M4, M5 |
| F4 | Interfaz conversacional minimalista | API FastAPI + UI web sencilla (Streamlit) | M9, M10 |
| F5 | Historial por ID de conversación, usando los **N** mensajes anteriores (N configurable) | Repositorio SQLite + ventana de N mensajes desde `.env` | M8 |

### 2.2 Requisitos obligatorios transversales

| ID | Requisito | Cómo se cumple | Módulo |
|---|---|---|---|
| R1 | Lenguaje Python | Python 3.11 en todo el backend | Todos |
| R2 | Dockerización completa, Dockerfile + docker-compose, **un solo comando** | `docker compose up -d --build` levanta Qdrant, ingesta inicial, API y UI | M0 (base), M12 |
| R3 | Repositorio público con historial lógico y commits descriptivos | Conventional Commits, una rama por módulo, tag al cerrar cada módulo | Todos |
| R4 | Al menos **3 patrones de diseño** documentados en el README | Se implementan 6 (ver §6); se documentan dónde y por qué | Todos / M14 |
| R5 | Historial de conversación: recordar contexto en sesión y **persistir** | SQLite en volumen Docker; se reconstruye contexto por `conversation_id` | M8 |
| R6 | Interfaz CLI, web sencilla o notebook; funcional y limpia | Streamlit (chat + analítica) y CLI de respaldo | M10 |
| R7 | Herramientas sin costo preferidas | Embeddings y reranker open source (Hugging Face) y Qdrant self-hosted, todo gratis y local. El LLM es **Gemini 2.5 Flash** con la clave gratuita de Google AI Studio (ADR-012, reemplaza a ADR-003); Grok (xAI, de pago) queda como alternativa. Es un servicio externo, aislado tras `LLMProvider` para poder cambiarlo por uno open source | M4–M7 |
| R8 | Análisis de datos: recorrer el histórico para extraer **métricas y valores de impacto** | Módulo de analítica (CLI + página en la UI + export CSV) | M11 |
| R9 | README completo (ver lista en §2.4) | README final redactado y verificado desde cero | M14 |

### 2.3 Bonus

| ID | Bonus | Cómo se cumple | Módulo |
|---|---|---|---|
| B1 | Reranker antes del LLM | Cross-encoder multilingüe sobre el top-k recuperado | M6 |
| B2 | Manejo de errores | Jerarquía de excepciones propia, reintentos con backoff, respuestas HTTP consistentes, degradación controlada | M0 (base) y todos |
| B3 | Configuración externalizada (`.env`) | `pydantic-settings`; N mensajes, modelo, chunk size, top-k, etc. | M0 y todos |

### 2.4 Contenido mínimo exigido del README

1. Requisitos previos (Docker, variables de entorno).
2. Paso a paso: clonar, configurar, levantar con Docker.
3. Cómo usar la interfaz conversacional.
4. Patrones de diseño: cuáles, dónde y por qué.
5. Stack tecnológico y justificación breve de cada decisión.
6. Limitaciones conocidas / decisiones de diseño relevantes.
7. Futuras mejoras.

> Notas del evaluador a respetar: no hay solución única; se valoran **decisiones razonadas y defendibles**; los atajos se declaran en el README (**la honestidad cuenta**); ante ambigüedad, **documentar el supuesto y avanzar** (ver §9).

---

## 3. Arquitectura

### 3.1 Flujo de ingesta (offline)

```
bancolombia.com ► Crawler ──► data/raw/ ──► Limpieza ──► data/clean/ ──► Chunking ──► Embeddings ──► Qdrant
              (robots,      (HTML +       (pipeline     (JSONL con     (por          (multilingüe)   (colección
               sitemap,      metadatos)    de pasos)     metadatos)     secciones)             bancolombia_docs)
               rate limit)
```

### 3.2 Flujo de consulta (online)

```
Usuario ─► UI (Streamlit) ─► API (FastAPI) ─► RAGService (Facade)
                                               │
                                               ├─ 1. Cargar últimos N mensajes del conversation_id (SQLite)
                                               ├─ 2. Reformular pregunta autónoma usando el historial
                                               ├─ 3. Recuperar top-k chunks (Qdrant)
                                               ├─ 4. Reranking → top-n (cross-encoder)
                                               ├─ 5. Generar respuesta con citas (Gemini vía API compatible con OpenAI)
                                               └─ 6. Persistir pregunta, respuesta, fuentes, latencias y scores
```

### 3.3 Servicios Docker

| Servicio | Imagen | Propósito | Persistencia |
|---|---|---|---|
| `qdrant` | `qdrant/qdrant:v1.19.1` | Base vectorial | volumen `qdrant_data` |
| `api` | build propio | FastAPI: chat, historial, ingesta, analítica | volúmenes `./data`, `history.db` |
| `ui` | build propio (misma imagen) | Streamlit: chat + panel de métricas | — |
| `init` (one-shot) | build propio | Ingesta (scrape → clean → index) si la colección está vacía | — |

> El LLM no corre en Docker: es un servicio externo (Gemini; Grok como alternativa) al que la `api` llama con `GEMINI_API_KEY` (o `XAI_API_KEY`).

---

## 4. Stack tecnológico (*PROPUESTA*)

| Capa | Elección | Justificación breve | Alternativa descartada |
|---|---|---|---|
| Lenguaje | Python 3.11 | Requisito; buen soporte de librerías ML | — |
| HTTP scraping | `httpx` + `BeautifulSoup4`/`lxml` | Sitio mayormente server-rendered; liviano y rápido | Playwright (solo si M1 detecta contenido renderizado por JS) |
| Extracción de texto | `trafilatura` + reglas propias | Elimina boilerplate (menús, footers) de forma robusta | Solo BeautifulSoup (más ruido) |
| Embeddings | `intfloat/multilingual-e5-small` (384 dim) | Gratis, multilingüe (español), corre en CPU | `bge-m3` (mejor calidad, mucho más pesado) |
| Base vectorial | Qdrant self-hosted | Gratis, Docker oficial, filtros por metadatos, modo `:memory:` para tests | Chroma (menos robusto como servicio), FAISS (no es servicio) |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Multilingüe, liviano, CPU | `bge-reranker-v2-m3` (más preciso, ~4× más pesado) |
| LLM | **Gemini 2.5 Flash** vía endpoint compatible con OpenAI (SDK `openai`); Grok (xAI) como alternativa (ADR-012) | Nivel gratuito, buena calidad en español, baja latencia, sin GPU/RAM local; el SDK `openai` permite cambiar de proveedor solo con configuración | Ollama + modelo open source (gratis pero lento en CPU; proveedor alternativo futuro) |
| Orquestación RAG | Código propio (sin LangChain) | Control total, patrones de diseño visibles y defendibles | LangChain/LlamaIndex (ocultan los patrones) |
| Historial | SQLite + SQLAlchemy | Cero infraestructura extra, persistente en volumen | Redis/Postgres (sobredimensionado) |
| API | FastAPI + Pydantic v2 | Validación, OpenAPI automático, TestClient | Flask |
| UI | Streamlit | Chat + dashboard en pocas líneas | Gradio, CLI pura |
| Configuración | `pydantic-settings` + `.env` | Validación de tipos de toda la configuración | `os.getenv` disperso |
| Calidad | `pytest`, `pytest-cov`, `respx`, `ruff` | Tests rápidos con mocks de HTTP; lint uniforme | — |

---

## 5. Estructura del repositorio (objetivo)

```
rag-bbva/
├── CLAUDE.md                     # Reglas para Claude Code
├── README.md
├── CHANGELOG.md
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── docs/
│   ├── 00_VISION_GENERAL.md      # Este documento
│   ├── 01_PLAN_DE_MODULOS.md
│   ├── 02_DECISIONES.md          # Registro de decisiones (ADR)
│   ├── exploracion_sitio.md      # Resultado de M1
│   └── modulos/                  # Una bitácora por módulo (M00.md, M01.md, …)
├── src/rag_bbva/
│   ├── config.py                 # Settings (pydantic-settings)
│   ├── exceptions.py
│   ├── logging_conf.py
│   ├── scraping/                 # crawler, robots, storage raw
│   ├── processing/               # limpieza, normalización
│   ├── indexing/                 # chunkers, embedders, vector store
│   ├── retrieval/                # retriever, reranker
│   ├── llm/                      # proveedores LLM, prompts
│   ├── memory/                   # repositorio de historial
│   ├── services/                 # RAGService (fachada)
│   ├── api/                      # FastAPI
│   ├── ui/                       # Streamlit
│   ├── analytics/                # métricas del historial
│   └── cli.py                    # comandos: scrape, clean, ingest, chat, metrics
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── eval/                         # golden set de preguntas (M13)
└── data/                         # ignorado en git (salvo muestras)
    ├── raw/
    ├── clean/
    └── history/
```

---

## 6. Patrones de diseño previstos

El caso pide mínimo 3. Se implementan 6 para tener margen, pero el README destacará los más defendibles.

| Patrón | Tipo | Dónde | Por qué |
|---|---|---|---|
| **Factory** | Creacional | `indexing/factory.py`, `llm/factory.py` | Crear embedder, LLM, vector store y reranker a partir de la configuración sin acoplar el resto del código a clases concretas |
| **Strategy** | Comportamental | `ChunkingStrategy`, `LLMProvider`, `Reranker` | Intercambiar algoritmos (chunking por secciones vs. tamaño fijo; Gemini (por defecto) vs. Grok / fake en tests; con/sin reranker) sin tocar el pipeline |
| **Repository** | Estructural / arquitectónico | `memory/repository.py` | Aislar la persistencia del historial; permite cambiar SQLite por otra BD y usar un repo en memoria en tests |
| **Facade** | Estructural | `services/rag_service.py` | Un único punto de entrada (`ask(conversation_id, pregunta)`) que orquesta memoria, recuperación, reranking y generación |
| **Template Method** | Comportamental | `scraping/base.py` | Esqueleto fijo del crawl (descubrir → descargar → validar → guardar) con pasos sobrescribibles |
| **Chain of Responsibility / Pipeline** | Comportamental | `processing/pipeline.py` | Limpieza como cadena de pasos independientes y testeables (quitar boilerplate, normalizar espacios, deduplicar…) |
| **Singleton (vía caché)** | Creacional | `config.get_settings()` con `lru_cache`, carga de modelos | Evitar recargar configuración y modelos pesados en cada petición |

---

## 7. Configuración externalizada (`.env`)

| Variable | Default propuesto | Descripción |
|---|---|---|
| `TARGET_BASE_URL` | `https://www.bancolombia.com/` | Sitio a scrapear (ADR-008) |
| `CRAWL_MAX_PAGES` | `1200` | Límite de páginas (cubre el sitemap completo, S-04; en desarrollo `--max-pages 50`) |
| `CRAWL_MAX_DEPTH` | `1` | Profundidad máxima del BFS desde las semillas del sitemap (S-04) |
| `CRAWL_DELAY_SECONDS` | `1.0` | Pausa entre peticiones (cortesía) |
| `CRAWL_USER_AGENT` | `RAG-BBVA-TechTest/1.0` | User-Agent identificable |
| `CRAWL_TIMEOUT_SECONDS` | `20` | Timeout por petición HTTP (agregada en M1) |
| `CRAWL_MAX_RETRIES` | `3` | Reintentos ante errores transitorios (5xx, timeouts) con backoff exponencial (M2) |
| `CRAWL_BACKOFF_SECONDS` | `2.0` | Espera base del backoff: 2, 4, 8 s… (M2) |
| `CRAWL_BLOCK_THRESHOLD` | `5` | Respuestas 403/429 consecutivas que abortan el crawl (M2) |
| `CRAWL_EXCLUDE_PATH_PREFIXES` | `["/acerca-de/sala-prensa/"]` | Prefijos de ruta que no se piden; quedan en el manifest como `excluida` (lista JSON; ADR-010, M3) |
| `RAW_DATA_DIR` | `data/raw` | Carpeta de HTML crudo y `manifest.jsonl` (M2) |
| `CLEAN_DATA_DIR` | `data/clean` | Carpeta de `documents.jsonl` y `clean_report.json` (M3) |
| `CLEAN_MIN_CHARS` | `200` | Mínimo de caracteres del texto limpio; los más cortos se descartan (M3) |
| `CLEAN_MIN_EXTRACTION_COVERAGE` | `0.9` | Fracción mínima del vocabulario del contenedor que debe conservar trafilatura; si no, *fallback* por selector (M3) |
| `CHUNKS_DATA_DIR` | `data/chunks` | Carpeta de `chunks.jsonl` y `chunk_report.json` (M4) |
| `CHUNKING_STRATEGY` | `heading_aware` | Estrategia de chunking (Strategy): `heading_aware` o `fixed_size` (M4) |
| `CHUNK_SIZE` | `800` | Tamaño de chunk (caracteres) |
| `CHUNK_OVERLAP` | `120` | Solapamiento |
| `CHUNK_MIN_CHARS` | `100` | Umbral de "chunk muy corto" en el reporte (M4) |
| `EMBEDDING_PROVIDER` | `sentence_transformers` | `sentence_transformers` o `fake` (tests) (M4) |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-small` | Modelo de embeddings |
| `EMBEDDING_BATCH_SIZE` | `32` | Textos por lote al embeber (M4) |
| `MODEL_CACHE_DIR` | `models` | Caché de modelos de Hugging Face, ignorada por git (M4; volumen en M12) |
| `QDRANT_URL` | `http://localhost:6333` | URL de Qdrant: el del compose publicado en la máquina local (M5); dentro de la red de Docker, `http://qdrant:6333` (M12) |
| `QDRANT_COLLECTION` | `bancolombia_docs` | Colección |
| `QDRANT_TIMEOUT_SECONDS` | `10` | Timeout de las llamadas a Qdrant (M5) |
| `QDRANT_BATCH_SIZE` | `128` | Puntos por lote en cada upsert (M5) |
| `EMBEDDINGS_CACHE_DIR` | `data/embeddings` | Caché de embeddings por hash del texto, ignorada por git (M5) |
| `RETRIEVAL_TOP_K` | `20` | Candidatos recuperados |
| `RERANKER_ENABLED` | `true` | Activar reranker |
| `RERANKER_MODEL` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Modelo de reranking |
| `RERANK_TOP_N` | `5` | Chunks finales al LLM |
| `RERANK_MAX_CHUNKS_PER_DOC` | `2` | Máximo de chunks de una misma página en el top-n; 0 = sin límite (M6) |
| `RERANK_MIN_SCORE` | `1.6` | Score mínimo del reranker (#1) para responder; por debajo, "sin información suficiente". Calibrado en M6 |
| `RERANK_HARD_MIN_SCORE` | `-3.0` | Umbral duro: debajo, "sin información" sin llamar al LLM; entre este y `RERANK_MIN_SCORE`, zona gris donde el LLM decide (M9, ADR-016) |
| `RERANKER_MAX_LENGTH` | `512` | Tokens máximos del par pregunta + fragmento en el cross-encoder (M6) |
| `RERANKER_BATCH_SIZE` | `16` | Pares por lote en el cross-encoder (M6) |
| `LLM_PROVIDER` | `gemini` | Proveedor (Strategy): `gemini`, `xai` o `fake` (tests) (ADR-012) |
| `GEMINI_API_KEY` | — (**obligatoria con `gemini`, secreta**) | Clave gratuita de Google AI Studio; solo en `.env`, nunca en git |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | Endpoint de Gemini compatible con OpenAI |
| `XAI_API_KEY` | — (**obligatoria con `xai`, secreta**) | Clave de la API de xAI; solo en `.env`, nunca en git |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | Endpoint de xAI compatible con OpenAI |
| `LLM_MODEL` | `gemini-2.5-flash` | Modelo; se verifica con `llm-check` |
| `LLM_FALLBACK_MODEL` | `gemini-3.1-flash-lite` | Respaldo solo ante 429 (cupo agotado) del modelo principal; vacío lo desactiva (M9, ADR-017) |
| `LLM_REASONING_EFFORT` | `none` | Solo Gemini: `none` apaga el razonamiento interno (menos latencia y tokens) |
| `LLM_TEMPERATURE` | `0.1` | Temperatura |
| `LLM_MAX_TOKENS` | `800` | Tope de tokens de salida (controla costo) |
| `LLM_TIMEOUT_SECONDS` | `20` | Tope de reloj por llamada al LLM; si se vence, reintento y luego respaldo (M10, ADR-017) |
| `LLM_TURN_BUDGET_SECONDS` | `45` | Presupuesto total del turno; si se agota, 503 "el servicio está lento" sin guardar (M10, ADR-017) |
| `LLM_MAX_RETRIES` | `2` | Reintentos propios ante 429/5xx/timeouts; el SDK no reintenta (M7) |
| `LLM_BACKOFF_SECONDS` | `1.0` | Espera base del backoff exponencial (M7) |
| `LLM_PRICE_INPUT_PER_MTOK` / `LLM_PRICE_OUTPUT_PER_MTOK` | `0.30` / `2.50` | USD por millón de tokens para estimar el costo equivalente: precios pagos de `gemini-2.5-flash` (ai.google.dev/gemini-api/docs/pricing). Con la clave gratuita el costo real es $0 (M7, ADR-012) |
| `QUERY_REWRITE_MODE` | `history_only` | Reformulación de la pregunta: `off`, `history_only` o `always` (M7) |
| `QUERY_REWRITE_MAX_TOKENS` | `120` | Tope de tokens de la pregunta reformulada (M7) |
| `HISTORY_DB_PATH` | `data/history/history.db` | Ruta SQLite |
| `HISTORY_WINDOW_N` | `6` | **N mensajes previos** usados como contexto |
| `MANUAL_SEARCH_MINUTES` | `5` | Supuesto para estimar tiempo ahorrado (analítica) |
| `ANALYTICS_TIMEZONE` | `America/Bogota` | Zona horaria de la distribución por día y hora (M11) |
| `ANALYTICS_FAQ_SIMILARITY` | `0.93` | Coseno mínimo para agrupar preguntas casi iguales; medido con e5 (M11.md §3) |
| `ANALYTICS_TOP_N` | `10` | Tamaño de los rankings de la analítica (M11) |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Dirección y puerto de `serve` (M9); en Docker, `0.0.0.0` (M12) |
| `CHAT_QUESTION_MAX_CHARS` | `1000` | Largo máximo de una pregunta; más largo responde 422 (M9) |
| `API_BASE_URL` | `http://127.0.0.1:8000` | URL de la API que consume la UI (M10); en Docker, `http://api:8000` (M12) |
| `UI_HOST` / `UI_PORT` | `127.0.0.1` / `8501` | Dirección y puerto de la UI Streamlit (`ui`, M10) |
| `UI_REQUEST_TIMEOUT_SECONDS` | `60` | Espera máxima de la UI por una respuesta de la API (M10) |
| `LOG_LEVEL` | `INFO` | Nivel de logs |

---

## 8. Analítica del historial (métricas y valores de impacto)

**Métricas operativas:** n.º de conversaciones, n.º de mensajes, mensajes por conversación (media/mediana), distribución por día/hora, latencia total y por etapa (recuperación, reranking, generación; p50/p95).

**Métricas de calidad:** % de respuestas "sin información suficiente", score medio del reranker, % de respuestas con al menos una fuente, valoraciones 👍/👎 del usuario (si se implementa feedback).

**Métricas de contenido:** preguntas más frecuentes (agrupadas por similitud), URLs/secciones más citadas, temas sin cobertura (preguntas con bajo score → oportunidades de contenido).

**Valores de impacto (estimados y declarados como tales):** consultas resueltas × `MANUAL_SEARCH_MINUTES` = horas de búsqueda manual ahorradas; tasa de resolución; usuarios/conversaciones recurrentes.

Salida: comando CLI `metrics`, endpoint `GET /analytics/summary`, página "Métricas" en Streamlit y export CSV.

---

## 9. Supuestos (documentados según la regla del caso)

| ID | Supuesto | Estado |
|---|---|---|
| S-01 | La fecha de entrega del PDF no aplica; se trabaja sin fecha límite fija y M13 (evaluación) entra en alcance | Confirmado |
| S-02 | Se usa el sitio de **Bancolombia** (`www.bancolombia.com`) como fuente de datos: `www.bbva.com.co` responde 403 (WAF) a `robots.txt`, home y sitemap para cualquier cliente no navegador. BBVA Colombia sigue siendo el cliente ficticio y el código conserva los nombres (`rag-bbva`, `rag_bbva`), pero **todo texto visible al usuario (prompts, UI, respuestas, README) dice Bancolombia** | Confirmado (2026-10-01, ADR-008) |
| S-03 | Alcance del scraping: páginas públicas HTML del dominio `www.bancolombia.com`: las 6 secciones principales (`personas`, `negocios`, `empresas`, `centro-de-ayuda`, `educacion-financiera`, `acerca-de`) y cualquier ruta del dominio alcanzada por enlace o redirección (p. ej. `/pagos`, `/tramites-digitales`); la `section` de cada documento se toma de su URL real (`final_url`). Se excluyen PDFs (además prohibidos por robots), formularios/solicitudes, áreas transaccionales/login, redirecciones a otros dominios (p. ej. `fiduciaria.bancolombia.com`) y URLs no HTML. La **sala de prensa** (`/acerca-de/sala-prensa/…`) queda **fuera**: sus URLs redirigen a otro host, `prensa.bancolombia.com` (ADR-010); el resto de `acerca-de` sigue dentro. `published_at` es un metadato opcional de M3, solo si la página trae la fecha en metadatos. Los PDFs y la sala de prensa pueden quedar como mejora futura | Confirmado (2026-10-01, checkpoint de M1; `docs/exploracion_sitio.md` §7). Acotado el 2026-10-02 (ADR-010) y ampliado a rutas alcanzadas por enlace o redirección el 2026-10-02 (ADR-011, revisión de M3) |
| S-04 | El crawl se limita (`CRAWL_MAX_PAGES`) para respetar al sitio y el tiempo de la prueba. Defaults: `CRAWL_MAX_PAGES=1200` (cubre las 1.113 URLs permitidas de los sitemaps) y `CRAWL_MAX_DEPTH=1`; en desarrollo `--max-pages 50`. El arranque con `docker compose` **no** scrapea: usa un snapshot versionado de datos limpios y el scraping completo es un comando opcional (M12) | Confirmado (2026-10-01, checkpoint de M1) |
| S-05 | "Usuarios internos" no implica autenticación. El `conversation_id` lo genera el servidor al crear la conversación (UUID4) y el cliente lo reenvía para continuarla; un ID desconocido se informa (404 en la API), no se crea al vuelo | Confirmado (2026-10-03, M8; contrato del ID en ADR-013) |
| S-06 | El asistente responde solo con el contexto recuperado; si no hay información suficiente, lo dice explícitamente (no inventa) | Propuesto |
| S-07 | El índice es una foto del sitio en una fecha; la actualización periódica queda como mejora futura | Propuesto |
| S-08 | Se respeta `robots.txt` y un delay entre peticiones; si `robots.txt` prohíbe rutas, se excluyen y se documenta | Propuesto |

---

## 10. Riesgos y mitigación

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Contenido cargado por JavaScript | Scraping vacío | M1 lo detecta; plan B con Playwright solo en las rutas afectadas |
| Bloqueo/rate limit del sitio (WAF) | Sin datos | Delay, reintentos con backoff, User-Agent claro, snapshot de `data/raw` versionado como muestra |
| Dependencia de un servicio externo (Gemini; Grok como alternativa) | Sin clave, sin cupo gratuito o sin saldo no hay respuestas; en el nivel gratuito Google puede usar las consultas para mejorar sus productos | `LLM_MAX_TOKENS`, contexto acotado (top-n chunks + N mensajes), error claro si falta la clave, `FakeLLMProvider` en tests (nunca gastan créditos) |
| Rate limit / caída de la API del LLM | Errores intermitentes | Reintentos con backoff en 429/5xx, timeout, mensaje amigable al usuario |
| Fuga de la API key | Riesgo de seguridad | `.env` en `.gitignore` desde el primer commit, `.env.example` sin valores, `SecretStr` en config |
| Imágenes Docker pesadas (torch) | Build lento | `torch` CPU-only, cache de modelos en volumen |
| Alucinaciones | Respuestas incorrectas | Prompt restrictivo, citas obligatorias, umbral mínimo de score para responder |
| Alcance creciente | Entrega incompleta | Módulos priorizados; atajos declarados en README |

---

## 11. Convenciones de trabajo

- **Desarrollo modular estricto:** un módulo solo se cierra cuando **todas sus pruebas pasan** y su bitácora está documentada (ver `01_PLAN_DE_MODULOS.md` → Definition of Done).
- **Git:** rama `feat/mXX-nombre` por módulo → merge a `main` → tag `mXX`. Commits en español con Conventional Commits: `feat(scraping): agrega crawler con soporte de robots.txt`.
- **Documentación viva:** cada módulo actualiza `docs/modulos/MXX.md`, `CHANGELOG.md` y, si aplica, `02_DECISIONES.md`.
- **Calidad:** `ruff check` sin errores y `pytest` en verde antes de cada merge.

---

## 12. Limitaciones conocidas

La tabla de limitaciones (L-01, L-02, …) **vive en el README**: [Limitaciones conocidas](../README.md#limitaciones-conocidas). Cada módulo agrega allí las suyas (Definition of Done, punto 7).
