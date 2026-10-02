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
| M3 | Limpieza (datos limpios) | ⏳ | — |
| M4 | Chunking + embeddings | ⏳ | — |
| M5 | Indexación vectorial (Qdrant) | ⏳ | — |
| M6 | Recuperación + reranker | ⏳ | — |
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

Diseño objetivo ([visión general §3](docs/00_VISION_GENERAL.md)). Hoy existe la primera etapa de la ingesta: **Crawler → `data/raw/`** (M2), además de la configuración, la CLI y la exploración del sitio (M0–M1).

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
uv pip install -e ".[dev]"
cp .env.example .env            # opcional por ahora: sin .env se usan los defaults

pytest                          # suite completa (sin red)
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
- `manifest.jsonl`: una línea por URL procesada, con `url`, `final_url`, `status`, `outcome` (`guardada`, `sin_cambios`, `duplicada`, `error_http`, `no_html`, `error_red`, `redireccion_omitida`), `depth`, `source`, `lastmod` del sitemap, huella del contenido, ruta del HTML, intentos y error.
- `crawl_report.json`: resumen de la corrida.

Comportamiento:
- 1 s de pausa entre peticiones y User-Agent identificable.
- Reintentos con backoff exponencial (2, 4, 8 s con la configuración por defecto: `CRAWL_MAX_RETRIES=3`, `CRAWL_BACKOFF_SECONDS=2.0`; tope de 60 s por espera). Solo se reintentan las respuestas HTTP **500, 502, 503 y 504** y los **errores de red de httpx** (`httpx.TransportError`: timeouts y fallos de conexión, lectura, escritura, protocolo o proxy). Cualquier otro código (incluidos 403, 404 y 429) se registra al primer intento. Si se agotan los intentos, una 5xx queda como `error_http` y un fallo de red como `error_red`, sin detener el crawl.
- Cada redirección se valida contra el dominio y `robots.txt`.
- URLs normalizadas: sin `utm_*`, fragmentos ni barra final.
- Si llegan 5 respuestas 403/429 seguidas, el crawl se aborta (código de salida 2) y se guarda lo avanzado.
- Re-ejecutarlo no reescribe los HTML cuyo texto visible no cambió.

Corrida real de referencia (50 páginas, ~77 s): [bitácora M02](docs/modulos/M02.md#6-evidencia-manual).

**Docker.** Hoy solo existe la imagen base: `docker build .` y `docker compose run --rm api` ejecutan el comando `version`.
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
| **Strategy** (inyección de dependencias) | [`src/rag_bbva/scraping/exploration.py`](src/rag_bbva/scraping/exploration.py): `Renderer` (`Protocol`); [`src/rag_bbva/scraping/storage.py`](src/rag_bbva/scraping/storage.py): función de huella inyectable en `RawStorage` | El explorador funciona con Playwright, con un doble o sin renderizador. El almacenamiento detecta cambios con la huella que se le inyecte (bytes por defecto, texto visible en el crawler) sin cambiar su código | ✅ parcial (M1–M2). Las estrategias principales (`ChunkingStrategy`, `LLMProvider`, `Reranker`) llegan en M4, M6 y M7 |
| **Template Method** | [`src/rag_bbva/scraping/base.py`](src/rag_bbva/scraping/base.py): `BaseCrawler.crawl()`; subclase concreta [`SitemapBfsCrawler`](src/rag_bbva/scraping/crawler.py) | `crawl()` fija el algoritmo (`prepare` → `discover_urls` → `fetch` → `validate` → `persist` → `extract_links`) y aplica en un solo lugar los límites, la deduplicación y el corte por bloqueo. Las subclases solo redefinen los pasos | ✅ M2 |
| **Chain of Responsibility / Pipeline** | `processing/pipeline.py` | Limpieza como cadena de pasos independientes y testeables | ⏳ M3 |
| **Factory** | `indexing/factory.py`, `llm/factory.py` | Crear embedder, LLM, vector store y reranker desde la configuración sin acoplarse a clases concretas | ⏳ M4–M7 |
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
| Extracción de texto | `trafilatura` + reglas propias | Elimina boilerplate de forma robusta | ⏳ M3 |
| Embeddings | `intfloat/multilingual-e5-small` | Gratis, multilingüe, corre en CPU ([ADR-005](docs/02_DECISIONES.md)) | ⏳ M4 |
| Base vectorial | Qdrant self-hosted | Gratis, Docker oficial, filtros por metadatos ([ADR-002](docs/02_DECISIONES.md)) | ⏳ M5 |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Multilingüe y liviano ([ADR-005](docs/02_DECISIONES.md)) | ⏳ M6 |
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
- **Alcance del scraping:** 6 secciones públicas (`personas`, `negocios`, `empresas`, `centro-de-ayuda`, `educacion-financiera`, `acerca-de`), sin PDFs, formularios ni otros dominios (S-03).
- **Sala de prensa fuera del alcance:** sus URLs redirigen a la portada de otro host, `prensa.bancolombia.com` ([ADR-010](docs/02_DECISIONES.md#adr-010--sala-de-prensa-fuera-del-alcance-del-scraping)).
- **Límites:** `CRAWL_MAX_PAGES=1200` (cubre el sitemap completo) y `CRAWL_MAX_DEPTH=1` (S-04).
- **Parser de `robots.txt` propio** (RFC 9309), porque el de la librería estándar no soporta los comodines `*`/`$` que usa Bancolombia.
- **Semillas intercaladas por sección:** un crawl parcial (`--max-pages 50`) cubre las 6 secciones en vez de solo la primera del sitemap (M2).
- **Cambios detectados por texto visible:** Bancolombia inyecta ids aleatorios en cada respuesta, así que el hash de bytes nunca coincidiría (M2).
- **Deduplicación por URL final:** muchas URLs de `empresas` redirigen a `/negocios`; se guarda una sola copia (M2).
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
| L-06 | **Sin sala de prensa (noticias y comunicados):** los sitemaps listan 74 URLs únicas bajo `/acerca-de/sala-prensa/`: 73 en `sitemap-sala-de-prensa.xml` y 1 solo en `sitemap-personas.xml`. Responden 301 hacia otro host, `prensa.bancolombia.com`. En el manifest de M2, la única procesada (1 de 1) redirige a la **portada** `https://prensa.bancolombia.com/`, no a la noticia, así que seguir la redirección no daría su contenido. Incluirlas exigiría explorar y crawlear un segundo sitio. Se excluyen: quedan como `redireccion_omitida`, sin HTML, y el asistente no responde sobre noticias. El resto de `acerca-de` sí se incluye | [ADR-010](docs/02_DECISIONES.md#adr-010--sala-de-prensa-fuera-del-alcance-del-scraping), M2 |
| L-07 | **URLs muertas en el sitemap:** algunas páginas listadas responden 403 `AccessDenied` (origen S3; p. ej. `/negocios/especiales/wobi…`). Se registran como `error_http` con un fragmento del cuerpo | M2 |
| L-08 | **Bloques que rotan:** varias páginas de educación financiera y del centro de ayuda muestran "artículos relacionados" aleatorios en cada petición, por lo que se reescriben aunque su contenido principal no cambie. Se quitarán en la limpieza (M3) | M2 |

---

## Futuras mejoras

Ideas registradas en las decisiones; ninguna está implementada:

- Proveedor LLM open source local (p. ej. vía Ollama) como alternativa a Grok, implementando otra estrategia de `LLMProvider` (ADR-003).
- Modelos de mayor calidad: `bge-m3` para embeddings y `bge-reranker-v2-m3` para reranking (ADR-005).
- Actualización periódica del índice en vez de una foto fija (S-07).
- Ingesta de PDFs públicos, si el sitio lo permitiera (S-03).
- Crawlear `prensa.bancolombia.com` como host adicional permitido, con su propio `robots.txt` y sitemap, para recuperar la sala de prensa y su fecha de publicación (ADR-010).
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
│   └── processing/ indexing/ retrieval/ llm/ memory/ services/ api/ ui/ analytics/   # vacíos (próximos módulos)
├── scripts/explore_site.py
├── tests/unit/ · tests/fixtures/ (robots, sitemaps, html) · tests/integration/
├── eval/                  # golden set (M13)
└── docs/
```

Documentación:
- [Visión general](docs/00_VISION_GENERAL.md): requisitos, arquitectura, configuración, supuestos.
- [Plan de módulos](docs/01_PLAN_DE_MODULOS.md): tareas, pruebas de aceptación y Definition of Done.
- [Decisiones (ADR)](docs/02_DECISIONES.md).
- [Exploración del sitio](docs/exploracion_sitio.md) y su [evidencia JSON](docs/evidencia/).
- Bitácoras por módulo: [M00](docs/modulos/M00.md) · [M01](docs/modulos/M01.md) · [M02](docs/modulos/M02.md).
- [CHANGELOG](CHANGELOG.md).
