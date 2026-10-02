# Visión general del proyecto — Asistente RAG sobre el sitio de BBVA Colombia

> Documento maestro del proyecto. Todo módulo, decisión y prueba debe ser trazable a este documento.
> Estado: **v0.3 — decisiones confirmadas** (LLM: Grok de xAI · UI: Streamlit · Entorno: Linux · Fuente: Bancolombia, ADR-008).

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
| R7 | Herramientas sin costo preferidas | Embeddings y reranker open source (Hugging Face) y Qdrant self-hosted, todo gratis y local. El LLM es **Grok (xAI)**, API de pago: válido según el caso pero no suma puntos → se declara en el README (ADR-003) y queda aislado tras `LLMProvider` para poder cambiarlo por uno open source | M4–M7 |
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
                                               ├─ 5. Generar respuesta con citas (Grok vía API de xAI)
                                               └─ 6. Persistir pregunta, respuesta, fuentes, latencias y scores
```

### 3.3 Servicios Docker

| Servicio | Imagen | Propósito | Persistencia |
|---|---|---|---|
| `qdrant` | `qdrant/qdrant` | Base vectorial | volumen `qdrant_data` |
| `api` | build propio | FastAPI: chat, historial, ingesta, analítica | volúmenes `./data`, `history.db` |
| `ui` | build propio (misma imagen) | Streamlit: chat + panel de métricas | — |
| `init` (one-shot) | build propio | Ingesta (scrape → clean → index) si la colección está vacía | — |

> El LLM no corre en Docker: es un servicio externo (API de xAI) al que la `api` llama con `XAI_API_KEY`.

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
| LLM | **Grok (xAI)** vía API compatible con OpenAI (`https://api.x.ai/v1`, SDK `openai`) | Calidad alta en español, sin GPU/RAM local, baja latencia; el SDK `openai` evita dependencias propietarias | Ollama + modelo open source (gratis pero lento en CPU; proveedor alternativo futuro) |
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
| **Strategy** | Comportamental | `ChunkingStrategy`, `LLMProvider`, `Reranker` | Intercambiar algoritmos (chunking por secciones vs. tamaño fijo; Grok vs. otro proveedor / fake en tests; con/sin reranker) sin tocar el pipeline |
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
| `CRAWL_MAX_PAGES` | `300` | Límite de páginas |
| `CRAWL_MAX_DEPTH` | `3` | Profundidad máxima del BFS |
| `CRAWL_DELAY_SECONDS` | `1.0` | Pausa entre peticiones (cortesía) |
| `CRAWL_USER_AGENT` | `RAG-BBVA-TechTest/1.0` | User-Agent identificable |
| `CRAWL_TIMEOUT_SECONDS` | `20` | Timeout por petición HTTP (agregada en M1) |
| `CHUNK_SIZE` | `800` | Tamaño de chunk (caracteres) |
| `CHUNK_OVERLAP` | `120` | Solapamiento |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-small` | Modelo de embeddings |
| `QDRANT_URL` | `http://qdrant:6333` | URL de Qdrant |
| `QDRANT_COLLECTION` | `bancolombia_docs` | Colección |
| `RETRIEVAL_TOP_K` | `20` | Candidatos recuperados |
| `RERANKER_ENABLED` | `true` | Activar reranker |
| `RERANKER_MODEL` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Modelo de reranking |
| `RERANK_TOP_N` | `5` | Chunks finales al LLM |
| `LLM_PROVIDER` | `xai` | Proveedor (Strategy): `xai` o `fake` (tests) |
| `XAI_API_KEY` | — (**obligatoria, secreta**) | Clave de la API de xAI; solo en `.env`, nunca en git |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | Endpoint compatible con OpenAI |
| `LLM_MODEL` | `grok-4.7` *(verificar en M7 con `GET /v1/models`)* | Modelo de Grok |
| `LLM_TEMPERATURE` | `0.1` | Temperatura |
| `LLM_MAX_TOKENS` | `800` | Tope de tokens de salida (controla costo) |
| `LLM_TIMEOUT_SECONDS` | `60` | Timeout por llamada |
| `HISTORY_DB_PATH` | `data/history/history.db` | Ruta SQLite |
| `HISTORY_WINDOW_N` | `6` | **N mensajes previos** usados como contexto |
| `MANUAL_SEARCH_MINUTES` | `5` | Supuesto para estimar tiempo ahorrado (analítica) |
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
| S-02 | Se usa el sitio de **Bancolombia** (`www.bancolombia.com`) como fuente de datos: `www.bbva.com.co` responde 403 (WAF) a `robots.txt`, home y sitemap para cualquier cliente no navegador. BBVA Colombia sigue siendo el cliente ficticio y se mantienen los nombres del proyecto (`rag-bbva`, `rag_bbva`) | Confirmado (2026-10-01, ADR-008) |
| S-03 | Alcance del scraping: páginas públicas HTML del dominio `www.bancolombia.com` (secciones `personas`, `negocios`, `empresas`, `centro-de-ayuda`, `educacion-financiera`, `acerca-de`); se excluyen PDFs (además prohibidos por robots), formularios/solicitudes, áreas transaccionales/login, redirecciones a otros dominios (p. ej. `fiduciaria.bancolombia.com`) y URLs no HTML. Los PDFs pueden quedar como mejora futura | Propuesto en M1 (`docs/exploracion_sitio.md` §7), **pendiente de validar con Juan Felipe** |
| S-04 | El crawl se limita (`CRAWL_MAX_PAGES`) para respetar al sitio y el tiempo de la prueba. M1 propone `CRAWL_MAX_PAGES=1200` (cubre las 1.113 URLs permitidas de los sitemaps) y `CRAWL_MAX_DEPTH=1` | Propuesto en M1, **pendiente de validar** |
| S-05 | "Usuarios internos" no implica autenticación; el `conversation_id` lo genera la UI o lo envía el cliente | Propuesto |
| S-06 | El asistente responde solo con el contexto recuperado; si no hay información suficiente, lo dice explícitamente (no inventa) | Propuesto |
| S-07 | El índice es una foto del sitio en una fecha; la actualización periódica queda como mejora futura | Propuesto |
| S-08 | Se respeta `robots.txt` y un delay entre peticiones; si `robots.txt` prohíbe rutas, se excluyen y se documenta | Propuesto |

---

## 10. Riesgos y mitigación

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Contenido cargado por JavaScript | Scraping vacío | M1 lo detecta; plan B con Playwright solo en las rutas afectadas |
| Bloqueo/rate limit del sitio (WAF) | Sin datos | Delay, reintentos con backoff, User-Agent claro, snapshot de `data/raw` versionado como muestra |
| Dependencia de un servicio externo de pago (xAI) | Sin clave o sin saldo no hay respuestas; costo por token | `LLM_MAX_TOKENS`, contexto acotado (top-n chunks + N mensajes), error claro si falta la clave, `FakeLLMProvider` en tests (nunca gastan créditos) |
| Rate limit / caída de la API de xAI | Errores intermitentes | Reintentos con backoff en 429/5xx, timeout, mensaje amigable al usuario |
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
