# RAG BBVA — Asistente conversacional sobre el sitio público de Bancolombia

> ⚠️ **Fuente de datos: Bancolombia, no BBVA.** `www.bbva.com.co` responde **403** (WAF) a `robots.txt`, a la home y al sitemap para cualquier cliente no navegador, así que no se puede scrapear de forma respetuosa. El caso permite usar otro banco y se eligió `www.bancolombia.com` (ver [ADR-008](docs/02_DECISIONES.md#adr-008--fuente-de-datos-bancolombia-en-lugar-de-bbva-colombia)). BBVA Colombia sigue siendo el cliente ficticio del caso: **el código conserva el nombre `rag_bbva`**, pero todo texto visible al usuario dice Bancolombia.

Prueba técnica de ML/AI Engineer: un sistema RAG (*Retrieval-Augmented Generation*) en Python que extrae por web scraping la información pública del sitio de Bancolombia, la guarda cruda y limpia, la indexa en una base vectorial y la expone mediante un asistente conversacional. El asistente responde solo con ese contenido, citando las fuentes y recordando el historial de cada conversación. El proyecto se construye por módulos (M0 a M14); este README describe **solo lo que ya está implementado** y marca lo pendiente.

---

## Estado del proyecto

✅ cerrado (merge a `main` + tag) · 🚧 en curso · ⏳ pendiente

| Módulo | Descripción | Estado | Tag |
|---|---|---|---|
| M0 | Fundaciones: estructura, configuración, excepciones, logging, CLI, Docker base | ✅ | `m00` |
| M1 | Exploración del sitio: robots.txt, sitemaps, dependencia de JS, alcance | ✅ | `m01` |
| M2 | Scraper (datos crudos): sitemaps + BFS, robots, reintentos, manifest incremental | ✅ | `m02` |
| M3 | Limpieza (datos limpios): pipeline de pasos, metadatos, idioma, deduplicación, chequeo de fugas | ✅ | `m03` |
| M4 | Chunking + embeddings: estrategias de chunking, e5-small en CPU, fábrica de componentes | ✅ | `m04` |
| M5 | Indexación vectorial (Qdrant): `ingest` idempotente con sincronización y caché de embeddings | ✅ | `m05` |
| M6 | Recuperación + reranker: cross-encoder, diversidad por página, umbral calibrado de "sin información" | 🚧 en revisión (rama `feat/m06-retrieval`) | — |
| M7 | Generación con LLM (Grok) | ⏳ | — |
| M8 | Memoria conversacional | ⏳ | — |
| M9 | Servicio RAG + API | ⏳ | — |
| M10 | Interfaz conversacional | ⏳ | — |
| M11 | Analítica del historial | ⏳ | — |
| M12 | Dockerización completa | ⏳ | — |
| M13 | Evaluación de calidad | ⏳ | — |
| M14 | Pulido final del README y verificación desde cero | ⏳ | — |

Detalle de cada módulo: [plan de módulos](docs/01_PLAN_DE_MODULOS.md) y bitácoras en [`docs/modulos/`](docs/modulos/).

---

## Arquitectura

Diseño objetivo ([visión general §3](docs/00_VISION_GENERAL.md)). Hoy existen la ingesta completa y la recuperación con reranker (M6): **Crawler → `data/raw/`** (M2), **Limpieza → `data/clean/`** (M3), **Chunking → `data/chunks/`** con embeddings en CPU (M4) e **indexación en Qdrant** (M5), además de la configuración, la CLI y la exploración del sitio (M0–M1).

**Ingesta (offline)**
```
bancolombia.com ► Crawler ──► data/raw/ ──► Limpieza ──► data/clean/ ──► Chunking ──► Embeddings ──► Qdrant
              (robots,      (HTML +       (pipeline     (JSONL con     (por          (multilingüe)   (colección
               sitemap,      metadatos)    de pasos)     metadatos)     secciones)             bancolombia_docs)
               rate limit)
```

**Consulta (online)**
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

---

## Requisitos previos

- **Linux** con **Docker** (Engine + Compose v2).
- **Python 3.11** y **[uv](https://docs.astral.sh/uv/)** para el entorno de desarrollo local. Si no tienes Python 3.11, `uv python install 3.11` lo instala.
- **git**.
- **Variables de entorno:** se copian de [`.env.example`](.env.example) a `.env`; ese archivo está en `.gitignore` y nunca se versiona. Toda la configuración está documentada ahí (scraping, chunking, Qdrant, LLM, historial, logging).
  - `XAI_API_KEY`: clave de la API de xAI (Grok). Solo hace falta desde M7 (generación); hoy ningún comando la usa. Va **únicamente** en `.env`.

---

## Instalación y ejecución

Lo que funciona hoy (verificado en un clon limpio):

```bash
git clone https://github.com/juanfero/rag-bbva.git
cd rag-bbva
uv venv --python 3.11
source .venv/bin/activate
# 1) torch CPU-only primero, desde el índice de PyTorch: evita bajar CUDA (~GB) desde PyPI
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
# 2) el proyecto y sus dependencias (torch ya instalado se respeta)
uv pip install -e ".[dev]"
cp .env.example .env            # opcional por ahora: sin .env se usan los defaults

pytest -m "not slow"            # suite rápida (sin red ni modelos)
pytest                          # incluye los tests `slow`: descarga una vez el modelo
                                # de embeddings (~470 MB) a MODEL_CACHE_DIR (models/)
ruff check . && ruff format --check .

python -m rag_bbva.cli version  # o: rag-bbva version
```

**Exploración del sitio (M1).** Descarga `robots.txt`, los sitemaps y una muestra de páginas respetando robots, con User-Agent identificable y 1 s de pausa. Deja `data/exploration/report.json` y `urls.txt`:
```bash
python scripts/explore_site.py --sample-size 10
```
El análisis de una corrida real está en [`docs/exploracion_sitio.md`](docs/exploracion_sitio.md).

**Scraping a datos crudos (M2).** Lee `robots.txt` (sin él no scrapea), toma las semillas de los dos índices de sitemap y sigue enlaces internos hasta `CRAWL_MAX_DEPTH`:
```bash
python -m rag_bbva.cli scrape --max-pages 50   # desarrollo; sin --max-pages usa CRAWL_MAX_PAGES=1200
```
Deja en `data/raw/` (ignorado por git):
- `pages/<sha1(url)>.html`: HTML tal como lo sirvió el sitio.
- `manifest.jsonl`: una línea por URL procesada, con `url`, `final_url`, `status`, `outcome` (`guardada`, `sin_cambios`, `duplicada`, `error_http`, `no_html`, `error_red`, `redireccion_omitida`, `excluida`), `depth`, `source`, `lastmod` del sitemap, huella del contenido, ruta del HTML, intentos y error.
- `crawl_report.json`: resumen de la corrida.

Comportamiento:
- 1 s de pausa entre peticiones y User-Agent identificable.
- Reintentos con backoff exponencial (2, 4, 8 s con la configuración por defecto: `CRAWL_MAX_RETRIES=3`, `CRAWL_BACKOFF_SECONDS=2.0`; tope de 60 s por espera). Solo se reintentan las respuestas HTTP **500, 502, 503 y 504** y los **errores de red de httpx** (`httpx.TransportError`: timeouts y fallos de conexión, lectura, escritura, protocolo o proxy). Cualquier otro código (incluidos 403, 404 y 429) se registra al primer intento. Si se agotan los intentos, una 5xx queda como `error_http` y un fallo de red como `error_red`, sin detener el crawl.
- Cada redirección se valida contra el dominio y `robots.txt`.
- URLs normalizadas: sin `utm_*`, fragmentos ni barra final.
- Las rutas de `CRAWL_EXCLUDE_PATH_PREFIXES` no se piden. Por defecto es `/acerca-de/sala-prensa/` ([ADR-010](docs/02_DECISIONES.md#adr-010--sala-de-prensa-fuera-del-alcance-del-scraping)). Quedan en el manifest como `excluida` y no consumen cupo de `--max-pages`.
- El manifest se escribe de forma incremental: una línea por URL apenas se procesa, forzada a disco. Al final se compacta. Qué queda en cada caso:
  - **Termina normal** (código 0): manifest y `crawl_report.json` completos.
  - **5 respuestas 403/429 seguidas** (código 2): el crawl se aborta y queda lo avanzado en el manifest y el reporte.
  - **Ctrl+C o `kill`/SIGTERM** (código 130): igual, con `abort_reason: "interrumpido"`.
  - **Error inesperado** (código 1): el manifest conserva lo avanzado; no hay reporte.
  - **`kill -9` o corte de energía:** no se puede atrapar. El manifest conserva todo lo ya escrito, no hay reporte y puede quedar como mucho un HTML sin su línea; `clean` lo ignora.
- Re-ejecutarlo no reescribe los HTML cuyo texto visible no cambió.

Corridas reales de referencia: 50 páginas en [M02](docs/modulos/M02.md#6-evidencia-manual) y crawl completo en [M03](docs/modulos/M03.md#6-evidencia-manual).

**Limpieza a datos limpios (M3).** Lee `data/raw/manifest.jsonl` (solo páginas con HTML guardado; URL canónica = URL final) y deja en `data/clean/` (ignorado por git):
```bash
python -m rag_bbva.cli clean
```
- `documents.jsonl`: un documento por página, con `doc_id`, `url`, `title`, `section`, `breadcrumbs`, `text` (markdown: títulos `#`, listas y tablas simples), `html_lang` (lo que declara la página), `lang` (idioma detectado en el texto), `lang_source`, `lastmod`, `published_at` (solo si la página lo trae en metadatos), `scraped_at`, `content_hash`, `n_chars`, `template` y `extraction`.
- `clean_report.json`: procesados, conservados, descartados por motivo (con la lista de URLs), distribución de longitudes, documentos por sección, plantilla, método de extracción e idioma, y el chequeo de **fugas de boilerplate**.

Cómo limpia (pasos en [`processing/steps.py`](src/rag_bbva/processing/steps.py)):
1. Quita del HTML la navegación, la cabecera y el pie, los menús de portlet con `${…}`, las cajas de error `.lrpError`, los iconos, el banner de cookies, el bloque rotativo de contenido relacionado (L-08), los bloques repetidos de venta cruzada ("Descubre otros canales…", "Si te gustó este producto…") y rótulos de interfaz como "Link copiado en porta papeles".
2. Toma el contenedor de cada plantilla del sitio (`main` → `#main-content` → `[role=main]`). Usa trafilatura si conserva al menos el 90 % del vocabulario del contenedor y el orden de sus bloques; si no, convierte el contenedor a markdown por selector.
3. Normaliza Unicode y espacios, y detecta el idioma.
4. Descarta los textos de menos de 200 caracteres y los duplicados (soft-404), registrando el motivo.

El resultado es determinista: la misma entrada produce la misma salida.

**Chunking y embeddings (M4).** Trocea `data/clean/documents.jsonl` y deja en `data/chunks/` (ignorado por git) `chunks.jsonl` y `chunk_report.json`:
```bash
python -m rag_bbva.cli chunk                          # estrategia de CHUNKING_STRATEGY (heading_aware)
python -m rag_bbva.cli chunk --strategy fixed_size    # línea base
```
- **`heading_aware`** (por defecto):
  - Divide por los títulos markdown de la limpieza y agrupa secciones pequeñas consecutivas hasta `CHUNK_SIZE`=800 caracteres.
  - Parte por tamaño, con `CHUNK_OVERLAP`=120 y sin cortar palabras, solo las secciones que no caben.
  - Cada chunk lleva su `heading_path` ("Título > Sección > Subsección").
- **`fixed_size`:** parte el texto completo por tamaño.
- **Qué guarda cada chunk:** `chunk_id` determinista, `doc_id`, `url`, `title`, `section`, `heading_path`, `lang`, `position`, `n_chars`, `text` (para citar) y `embedding_text` (encabezado con título, sección y ruta + texto: lo que se embebe).
- **El reporte** trae la distribución de tamaños, los chunks por documento y por sección, los chunks muy cortos y los que superarían los 512 tokens del modelo, contados con su tokenizer real. En la corrida real: 3506 chunks y 0 sobre el máximo.
- **Embeddings:** `intfloat/multilingual-e5-small` (384 dimensiones) en CPU, con prefijos `query: `/`passage: ` y vectores normalizados (L2).
  - El modelo se descarga una vez (~470 MB) a `MODEL_CACHE_DIR` (`models/`, ignorado por git).
  - Embeber los ~3500 chunks toma ~2,6 min en CPU. Con el modelo ya en caché se carga sin consultar Hugging Face ([evidencia M04](docs/modulos/M04.md#6-evidencia-manual)).
  - La base vectorial se llena con `ingest` (M5).

**Indexación en Qdrant (M5).** Levanta Qdrant (imagen fija `qdrant/qdrant:v1.19.1`, volumen `qdrant_data`, puerto solo en `127.0.0.1:6333`) e indexa los documentos limpios:
```bash
docker compose up -d qdrant                 # espera a que quede "healthy" (docker compose ps)
python -m rag_bbva.cli ingest               # chunks → embeddings (con caché) → Qdrant
python -m rag_bbva.cli ingest --recreate    # borra la colección y la reconstruye desde cero
```
- **Idempotente:** cada chunk se guarda con un id determinista (`uuid5` de su `chunk_id`).
  - Re-ingestar no duplica.
  - Los chunks cuyo texto no cambió no se re-embeben ni se reescriben.
  - Los puntos de chunks que ya no existen se **borran** (sincronización completa).
- **Caché de embeddings** en `EMBEDDINGS_CACHE_DIR` (`data/embeddings/`, ignorada por git): `--recreate` o un cambio de ids reutilizan los vectores ya calculados.
- **Reporte:** chunks, nuevos, actualizados, sin cambios, eliminados, embebidos, desde caché, puntos y tiempos por etapa.
- **Corrida real:** 3506 puntos en 2,5 min (casi todo embeddings en CPU). La re-ingesta sin cambios toma 0,2 s y no embebe nada; con Qdrant vacío y la caché llena, `--recreate` reconstruye los 3506 puntos en 1,6 s ([evidencia M05](docs/modulos/M05.md#6-evidencia-manual)).
- **Configuración:** por defecto `QDRANT_URL=http://localhost:6333` (el Qdrant del compose); dentro de la red de Docker (M12) será `http://qdrant:6333`. Si Qdrant no responde, `ingest` termina con un error claro (código 1).

**Búsqueda con reranker (M6, bonus).** Con Qdrant levantado e indexado:
```bash
python -m rag_bbva.cli search "¿cómo descargo un comprobante de transferencia?"
python -m rag_bbva.cli search "¿qué es un CDT?" --section negocios --top-n 3
python -m rag_bbva.cli search "receta de arepas de queso" --no-rerank   # solo similitud coseno
```
Cómo funciona la búsqueda:
1. Embebe la pregunta (`query: `) y trae los 20 chunks más parecidos por coseno (`RETRIEVAL_TOP_K`).
2. Un **cross-encoder** multilingüe (`RERANKER_MODEL`) lee la pregunta y cada fragmento juntos y les da un score de relevancia.
3. Queda el top-5 (`RERANK_TOP_N`), con como mucho 2 chunks por página (`RERANK_MAX_CHUNKS_PER_DOC`).

`search` muestra para cada resultado la URL, el `heading_path`, el score del reranker, el coseno y de qué puesto venía. También indica si el #1 supera el umbral y la latencia de cada etapa.

**Por qué el reranker (bonus).** El coseno compara vectores calculados por separado. El cross-encoder ve pregunta y texto a la vez, y eso mejora el orden:
- En "requisitos para crédito de vivienda", el chunk de requisitos sube del puesto 20 al 1.
- En "¿cómo descargo un comprobante…?", los pasos concretos pasan por delante de la introducción.

**Umbral de "sin información suficiente"** (`RERANK_MIN_SCORE=1.6`). Si el #1 no lo alcanza, el asistente debe decir que no tiene información en vez de inventar.
- Se calibró con `eval/calibration.jsonl`: 15 preguntas que el sitio responde y 15 que no (fuera de dominio, otros bancos, prensa, simuladores). Acierta 27 de 30.
- Va sobre el score del reranker y **no sobre el coseno**: los cosenos de e5 están comprimidos (≈ 0,79–0,92 para todo). El mejor umbral posible sobre el coseno acierta 24 de 30 y queda pegado a los datos (margen 0,001).
- Sin reranker (`--no-rerank` o `RERANKER_ENABLED=false`) no se aplica umbral.
- **Latencia en CPU:** retrieval ~19 ms; rerank ~0,9 s (p50).
- Detalle y casos en la [bitácora M06](docs/modulos/M06.md#6-evidencia-manual).

**Docker.** Hoy existen la imagen base (`docker build .`; `docker compose run --rm api` ejecuta el comando `version`) y el servicio `qdrant` para desarrollo (`docker compose up -d qdrant`).
🚧 **El despliegue completo con `docker compose up -d --build` (Qdrant, API, UI) se completa en M12.** Ese arranque no scrapeará el sitio: usará un snapshot versionado de datos limpios.

---

## Uso de la interfaz conversacional

🚧 **Se completa en M10** (Streamlit + CLI de respaldo). Hoy no existe ninguna interfaz conversacional.

---

## Patrones de diseño

El caso exige al menos 3. Previstos en la [visión general §6](docs/00_VISION_GENERAL.md):

| Patrón | Dónde | Por qué | Estado |
|---|---|---|---|
| **Singleton (vía caché)** | [`src/rag_bbva/config.py`](src/rag_bbva/config.py): `get_settings()` con `lru_cache` | La configuración se lee y valida una sola vez; todo el código la obtiene del mismo punto | ✅ M0 |
| **Strategy** (inyección de dependencias) | [`src/rag_bbva/scraping/exploration.py`](src/rag_bbva/scraping/exploration.py): `Renderer` (`Protocol`); [`src/rag_bbva/scraping/storage.py`](src/rag_bbva/scraping/storage.py): función de huella inyectable en `RawStorage` | El explorador funciona con Playwright, con un doble o sin renderizador. El almacenamiento detecta cambios con la huella que se le inyecte (bytes por defecto, texto visible en el crawler) sin cambiar su código | ✅ parcial (M1–M2) |
| **Strategy** (algoritmos intercambiables) | [`src/rag_bbva/indexing/chunking.py`](src/rag_bbva/indexing/chunking.py): `ChunkingStrategy` → `HeadingAwareChunker` / `FixedSizeChunker`; [`src/rag_bbva/indexing/embedding.py`](src/rag_bbva/indexing/embedding.py): `Embedder` → `SentenceTransformerEmbedder` / `FakeEmbedder` | Cambiar cómo se trocea o cómo se embebe sin tocar el pipeline: la línea base de chunking se compara con la principal y los tests usan un embedder falso, sin modelo | ✅ M4 |
| **Strategy** (reranking) | [`src/rag_bbva/retrieval/reranker.py`](src/rag_bbva/retrieval/reranker.py): `Reranker` → `CrossEncoderReranker` / `NoOpReranker`; la fábrica elige según `RERANKER_ENABLED` | Activar o desactivar el reranker sin tocar el `Retriever`; los tests usan un reranker determinista | ✅ M6. `LLMProvider` llega en M7 |
| **Template Method** | [`src/rag_bbva/scraping/base.py`](src/rag_bbva/scraping/base.py): `BaseCrawler.crawl()`; subclase concreta [`SitemapBfsCrawler`](src/rag_bbva/scraping/crawler.py) | `crawl()` fija el algoritmo (`prepare` → `discover_urls` → `fetch` → `validate` → `persist` → `extract_links`) y aplica en un solo lugar los límites, la deduplicación y el corte por bloqueo. Las subclases solo redefinen los pasos | ✅ M2 |
| **Chain of Responsibility / Pipeline** | [`src/rag_bbva/processing/steps.py`](src/rag_bbva/processing/steps.py): `CleaningStep` (`set_next`/`handle`) y sus pasos; [`src/rag_bbva/processing/pipeline.py`](src/rag_bbva/processing/pipeline.py): `CleaningPipeline` | Cada paso de la limpieza (parseo, metadatos, boilerplate, extracción, normalización, idioma, longitud, duplicados) es una clase que transforma el documento y lo pasa al siguiente, o corta la cadena con el motivo del descarte. Se prueban por separado y se pueden reordenar o sustituir | ✅ M3 |
| **Factory** | [`src/rag_bbva/indexing/factory.py`](src/rag_bbva/indexing/factory.py): `ComponentFactory` (`create_chunker`, `create_embedder`); `llm/factory.py` | Crear chunker y embedder (luego LLM, vector store y reranker) desde la configuración (`CHUNKING_STRATEGY`, `EMBEDDING_PROVIDER`) sin acoplar el resto del código a clases concretas | ✅ parcial (M4). LLM, vector store y reranker: M5–M7 |
| **Adapter** (puerto de la base vectorial) | [`src/rag_bbva/indexing/vector_store.py`](src/rag_bbva/indexing/vector_store.py): interfaz `VectorStore` → `QdrantVectorStore` | La ingesta (y la recuperación de M6) hablan con una interfaz propia: `ensure_collection`, `upsert`, `search` con filtro por sección, `count`, `delete`. El adaptador traduce a `qdrant-client` y sus errores a `IndexingError`. Los tests usan el mismo adaptador sobre `QdrantClient(":memory:")` | ✅ M5 |
| **Repository** | `memory/repository.py` | Aislar la persistencia del historial (SQLite en producción, memoria en tests) | ⏳ M8 |
| **Facade** | `services/rag_service.py` | Un único punto de entrada `ask(conversation_id, pregunta)` que orquesta todo el flujo | ⏳ M9 |

---

## Stack tecnológico

| Capa | Elección | Justificación | Estado |
|---|---|---|---|
| Lenguaje | Python 3.11 | Requisito del caso; ecosistema ML | ✅ en uso |
| HTTP / parsing | `httpx` + `BeautifulSoup4` / `lxml` | El sitio es server-rendered: no hace falta navegador ([ADR-009](docs/02_DECISIONES.md)) | ✅ en uso (M1) |
| Renderizado JS | Playwright | Solo se usó, de forma temporal, para medir la dependencia de JS en M1; no es dependencia del proyecto ([ADR-009](docs/02_DECISIONES.md)) | descartado |
| Configuración | `pydantic-settings` + `.env` | Toda la configuración tipada y validada en un solo lugar | ✅ en uso (M0) |
| CLI | Typer | Comandos con ayuda y validación automáticas | ✅ en uso (M0) |
| Reintentos | `tenacity` | Backoff exponencial declarativo, solo ante errores transitorios | ✅ en uso (M2) |
| Calidad | `pytest`, `respx`, `ruff` | Tests rápidos sin red (`respx` simula el sitio por HTTP); lint y formato uniformes | ✅ en uso |
| Extracción de texto | `trafilatura` + reglas propias | Reglas por selector para el boilerplate conocido del sitio y trafilatura para el contenido principal, con *fallback* por selector cuando omite contenido | ✅ en uso (M3) |
| Embeddings | `intfloat/multilingual-e5-small` vía `sentence-transformers` | Gratis, multilingüe, corre en CPU; 384 dimensiones ([ADR-005](docs/02_DECISIONES.md)) | ✅ en uso (M4) |
| Cómputo de modelos | `torch` CPU-only | Instalado desde el índice CPU de PyTorch: sin CUDA, para una imagen Docker liviana (M12) | ✅ en uso (M4) |
| Base vectorial | Qdrant self-hosted `v1.19.1` + `qdrant-client` 1.19 | Gratis, Docker oficial, filtros por metadatos ([ADR-002](docs/02_DECISIONES.md)) | ✅ en uso (M5; compose de desarrollo) |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Multilingüe y liviano, corre en CPU (470 MB) ([ADR-005](docs/02_DECISIONES.md)) | ✅ en uso (M6) |
| LLM | Grok (xAI) vía SDK `openai` | Calidad en español sin GPU local; es de pago y queda aislado tras una interfaz ([ADR-003](docs/02_DECISIONES.md)) | ⏳ M7 |
| Orquestación RAG | Código propio, sin LangChain | Patrones visibles y testeables ([ADR-001](docs/02_DECISIONES.md)) | ⏳ M9 |
| Historial | SQLite + SQLAlchemy | Cero infraestructura extra, persistente ([ADR-004](docs/02_DECISIONES.md)) | ⏳ M8 |
| API / UI | FastAPI · Streamlit | Validación y OpenAPI · chat y panel en pocas líneas | ⏳ M9 / M10 |
| Contenedores | Docker + Compose | Requisito del caso | ✅ imagen base (M0) · ⏳ completo en M12 |

---

## Decisiones de diseño y supuestos

Registro completo: [`docs/02_DECISIONES.md`](docs/02_DECISIONES.md) (ADR) y supuestos en la [visión general §9](docs/00_VISION_GENERAL.md). Las más relevantes hasta ahora:

- **Fuente Bancolombia** en lugar de BBVA porque bbva.com.co bloquea crawlers (ADR-008, S-02).
- **Sin navegador headless:** el contenido principal está en el HTML estático; medido sobre 12 páginas renderizadas (ADR-009).
- **Alcance del scraping:** las 6 secciones públicas principales (`personas`, `negocios`, `empresas`, `centro-de-ayuda`, `educacion-financiera`, `acerca-de`) y cualquier ruta del dominio alcanzada por enlace o redirección, sin PDFs, formularios ni otros dominios. La sección de cada documento sale de su URL real (S-03, [ADR-011](docs/02_DECISIONES.md#adr-011--alcance-secciones-principales-más-rutas-del-dominio-alcanzadas)).
- **Sala de prensa fuera del alcance:** sus URLs redirigen a la portada de otro host, `prensa.bancolombia.com` ([ADR-010](docs/02_DECISIONES.md#adr-010--sala-de-prensa-fuera-del-alcance-del-scraping)).
- **Límites:** `CRAWL_MAX_PAGES=1200` (cubre el sitemap completo) y `CRAWL_MAX_DEPTH=1` (S-04).
- **Parser de `robots.txt` propio** (RFC 9309), porque el de la librería estándar no soporta los comodines `*`/`$` que usa Bancolombia.
- **Semillas intercaladas por sección:** un crawl parcial (`--max-pages 50`) cubre las 6 secciones en vez de solo la primera del sitemap (M2).
- **Cambios detectados por texto visible:** Bancolombia inyecta ids aleatorios en cada respuesta, así que el hash de bytes nunca coincidiría (M2).
- **Deduplicación por URL final:** muchas URLs de `empresas` redirigen a `/negocios`; se guarda una sola copia (M2).
- **Boilerplate por reglas antes de extraer:** trafilatura sola sobre la página completa tomaba el bloque rotativo como título. Además, la plantilla de WebSphere mete el menú del sitio dentro del contenedor principal (M3).
- **trafilatura solo si conserva ≥ 90 % del vocabulario:** en el HTML real omitió títulos y secciones enteras en 19 de 30 páginas, sin agregar nunca texto propio (M3).
- **Umbral sobre el reranker, no sobre el coseno:** el coseno de e5 no separa preguntas respondibles de las que no lo son; el score del cross-encoder sí, en una escala de ~17 puntos (M6).
- **`html_lang` y `lang` separados:** la plantilla de WebSphere declara `lang="en"` en páginas en español. `lang` se detecta en el texto por palabras funcionales, sin dependencias nuevas (M3).
- **`XAI_API_KEY` opcional** al cargar la configuración; se exige al crear el proveedor del LLM (ADR-006).
- **Dependencias incrementales:** cada módulo agrega solo lo que usa (ADR-007).
- **Orquestación propia, sin LangChain** (ADR-001).

---

## Limitaciones conocidas

| ID | Limitación | Origen |
|---|---|---|
| L-01 | **Fuente de datos distinta al cliente:** el contenido es de `www.bancolombia.com`, no de `www.bbva.com.co`, que bloquea con 403 (WAF) a todo cliente no navegador. El código conserva el nombre `rag_bbva`; los textos visibles dicen Bancolombia | ADR-008 |
| L-02 | **Política de bots de IA de Bancolombia:** su `robots.txt` bloquea por completo a los bots de *entrenamiento* de IA (GPTBot, ClaudeBot, Google-Extended, Applebot-Extended, cohere-ai). Este proyecto no entrena modelos: hace recuperación (RAG) con un User-Agent identificable (`RAG-BBVA-TechTest/1.0`) que respeta las reglas del grupo `*` y espera 1 s entre peticiones | M1, [`docs/exploracion_sitio.md`](docs/exploracion_sitio.md) §1 |
| L-03 | **Contenido dinámico no capturado:** sin renderizar JS no se obtienen el banner de cookies, carruseles ni listas de enlaces dinámicas; su contenido llega por las páginas enlazadas | ADR-009 |
| L-04 | **Sin PDFs:** quedan fuera del alcance y además `robots.txt` los prohíbe (`/*pdf*`) | S-03 |
| L-05 | **Foto del sitio:** el índice refleja el sitio en la fecha del scraping; la demo usará un snapshot versionado de datos limpios | S-07, M12 |
| L-06 | **Sin sala de prensa (noticias y comunicados):** los sitemaps listan 74 URLs únicas bajo `/acerca-de/sala-prensa/`: 73 en `sitemap-sala-de-prensa.xml` y 1 solo en `sitemap-personas.xml`. Responden 301 hacia otro host, `prensa.bancolombia.com`. En el manifest de M2, la única procesada (1 de 1) redirige a la **portada** `https://prensa.bancolombia.com/`, no a la noticia, así que seguir la redirección no daría su contenido. Incluirlas exigiría explorar y crawlear un segundo sitio. Se excluyen sin pedirlas (`CRAWL_EXCLUDE_PATH_PREFIXES`, desde M3; quedan como `excluida`), y el asistente no responde sobre noticias. El resto de `acerca-de` sí se incluye | [ADR-010](docs/02_DECISIONES.md#adr-010--sala-de-prensa-fuera-del-alcance-del-scraping), M2 |
| L-07 | **URLs muertas en el sitemap:** algunas páginas listadas responden 403 `AccessDenied` (origen S3; p. ej. `/negocios/especiales/wobi…`). Se registran como `error_http` con un fragmento del cuerpo | M2 |
| L-08 | **Bloques que rotan (resuelta en la limpieza, M3):** varias páginas de educación financiera y del centro de ayuda muestran "contenido relacionado" aleatorio en cada petición. Por eso se reescribe su HTML crudo aunque el contenido principal no cambie. La limpieza quita ese bloque: en el crawl completo, 34 páginas lo traían y en ninguna quedó en el texto limpio | M2, M3 |
| L-09 | **Simuladores y páginas cargadas por JS, sin contenido:** su contenido llega por JavaScript, así que el HTML estático no tiene texto propio. La limpieza las descarta como `texto_corto` (menos de `CLEAN_MIN_CHARS`=200 caracteres) y **el asistente no podrá responder sobre ellas**. En el crawl completo fueron 57 de 685 páginas: 10 simuladores y calculadoras, 17 páginas de resultados de búsqueda de preguntas frecuentes y 30 páginas con contenido por JS o solo con errores de WCM, entre ellas el buscador de puntos de atención. Otros 4 simuladores muestran solo la pantalla de ingreso (`/personas/login`) y se descartan como duplicados. El listado está en `data/clean/clean_report.json` (`discarded_documents`) | ADR-009, M3 |
| L-10 | **Cobertura del enlace a enlace (BFS) acotada (aceptada, sin volver a crawlear):** con `CRAWL_MAX_PAGES=1200` se procesan todas las semillas del sitemap, pero solo 173 de los 639 enlaces internos de profundidad 1 encontrados. Además, 250 URLs retiradas redirigen a la portada de su sección (p. ej. `…/sostenibilidad/novacampo` → `/personas`) y consumen cupo, aunque no generan documentos (un solo documento por portada) | M3 |

---

## Futuras mejoras

Ideas registradas en las decisiones; ninguna está implementada:

- Proveedor LLM open source local (p. ej. vía Ollama) como alternativa a Grok, implementando otra estrategia de `LLMProvider` (ADR-003).
- Modelos de mayor calidad: `bge-m3` para embeddings y `bge-reranker-v2-m3` para reranking (ADR-005).
- Actualización periódica del índice en vez de una foto fija (S-07).
- Ingesta de PDFs públicos, si el sitio lo permitiera (S-03).
- Crawlear `prensa.bancolombia.com` como host adicional permitido, con su propio `robots.txt` y sitemap, para recuperar la sala de prensa y su fecha de publicación (ADR-010).
- Que el cupo de `--max-pages` cuente solo los HTML únicos guardados, no los duplicados ni las redirecciones, para que el BFS llegue más lejos con el mismo límite (L-10).
- Base de historial apta para alta concurrencia (Postgres/Redis) en lugar de SQLite (ADR-004).
- Reevaluar el renderizado con JS si el sitio migra a una SPA, con el mismo `scripts/explore_site.py --render` (ADR-009).

---

## Estructura del repositorio

Estado actual (crece con cada módulo):

```
rag-bbva/
├── README.md · CHANGELOG.md · CLAUDE.md
├── pyproject.toml · .env.example · Dockerfile · docker-compose.yml
├── src/rag_bbva/
│   ├── config.py · exceptions.py · logging_conf.py · cli.py
│   ├── scraping/          # robots, sitemap, urls, fetcher, discovery, storage, base (Template Method), crawler, page_analysis, exploration
│   ├── processing/        # models, markdown, steps (Chain of Responsibility), pipeline, quality
│   ├── indexing/          # models, chunking (Strategy), embedding, embedding_cache, factory (Factory), pipeline, vector_store (Adapter), ingest
│   ├── retrieval/         # models, reranker (Strategy), retriever, calibration
│   └── llm/ memory/ services/ api/ ui/ analytics/   # vacíos (próximos módulos)
├── scripts/explore_site.py · scripts/trim_html_fixture.py · scripts/calibrate_reranker.py
├── tests/unit/ · tests/fixtures/ (robots, sitemaps, html: páginas reales recortadas de las 3 plantillas; clean: glosario limpio) · tests/integration/
├── eval/                  # calibration.jsonl (M6: umbral del reranker); golden set (M13)
└── docs/
```

Documentación:
- [Visión general](docs/00_VISION_GENERAL.md): requisitos, arquitectura, configuración, supuestos.
- [Plan de módulos](docs/01_PLAN_DE_MODULOS.md): tareas, pruebas de aceptación y Definition of Done.
- [Decisiones (ADR)](docs/02_DECISIONES.md).
- [Exploración del sitio](docs/exploracion_sitio.md) y su [evidencia JSON](docs/evidencia/).
- Bitácoras por módulo: [M00](docs/modulos/M00.md) · [M01](docs/modulos/M01.md) · [M02](docs/modulos/M02.md) · [M03](docs/modulos/M03.md) · [M04](docs/modulos/M04.md) · [M05](docs/modulos/M05.md) · [M06](docs/modulos/M06.md).
- [CHANGELOG](CHANGELOG.md).
