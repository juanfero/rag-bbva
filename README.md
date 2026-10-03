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
| M6 | Recuperación + reranker: cross-encoder, diversidad por página, umbral calibrado de "sin información" | ✅ | `m06` |
| M7 | Generación con LLM: Gemini 2.5 Flash (Grok como alternativa), prompts versionados, citas, reformulación | ✅ | `m07` |
| M8 | Memoria conversacional: historial en SQLite (Repository), últimos N mensajes, métricas por mensaje | ✅ | `m08` |
| M9 | Servicio RAG + API: fachada `RAGService`, FastAPI, turno atómico, umbral doble, LLM de respaldo, `/health` | ✅ | `m09` |
| M10 | Interfaz conversacional: Streamlit sobre la API, fuentes, 👍/👎, modo detalle, CLI `chat` | 🟡 en revisión | — |
| M11 | Analítica del historial | ⏳ | — |
| M12 | Dockerización completa | ⏳ | — |
| M13 | Evaluación de calidad | ⏳ | — |
| M14 | Pulido final del README y verificación desde cero | ⏳ | — |

Detalle de cada módulo: [plan de módulos](docs/01_PLAN_DE_MODULOS.md) y bitácoras en [`docs/modulos/`](docs/modulos/).

---

## Arquitectura

Diseño objetivo ([visión general §3](docs/00_VISION_GENERAL.md)). Hoy existen la ingesta completa, la recuperación con reranker (M6) y la generación con citas (M7), el historial de conversaciones en SQLite (M8) y la API REST que los une a través de la fachada `RAGService` (M9): **Crawler → `data/raw/`** (M2), **Limpieza → `data/clean/`** (M3), **Chunking → `data/chunks/`** con embeddings en CPU (M4) e **indexación en Qdrant** (M5), además de la configuración, la CLI y la exploración del sitio (M0–M1).

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
                                               ├─ 5. Generar respuesta con citas (Gemini 2.5 Flash; Grok como alternativa)
                                               └─ 6. Persistir pregunta, respuesta, fuentes, latencias y scores
```

---

## Requisitos previos

- **Linux** con **Docker** (Engine + Compose v2).
- **Python 3.11** y **[uv](https://docs.astral.sh/uv/)** para el entorno de desarrollo local. Si no tienes Python 3.11, `uv python install 3.11` lo instala.
- **git**.
- **Variables de entorno:** se copian de [`.env.example`](.env.example) a `.env`; ese archivo está en `.gitignore` y nunca se versiona. Toda la configuración está documentada ahí (scraping, chunking, Qdrant, LLM, historial, logging).
  - `GEMINI_API_KEY`: clave del LLM (Gemini). Se obtiene **gratis** en https://aistudio.google.com/api-keys. Va **únicamente** en `.env`, nunca en el código ni en git. Hace falta desde M7 (generación).
  - `XAI_API_KEY`: solo si se usa Grok como alternativa (`LLM_PROVIDER=xai`, de pago).

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

**Umbral de "sin información suficiente"** (`RERANK_MIN_SCORE=1.6`). Si el #1 no lo alcanza, el asistente debe decir que no tiene información en vez de inventar. Desde M9 es un **umbral doble** ([ADR-016](docs/02_DECISIONES.md)):
- Por debajo de `RERANK_HARD_MIN_SCORE=-3.0` responde "sin información" **sin llamar al LLM** (fuera de dominio claro).
- Entre −3,0 y 1,6 (**zona gris**) el LLM recibe el contexto y responde o se abstiene. Si se abstiene, empieza con la marca `[SIN_INFO]`, que el sistema quita y registra como `no_answer`. Lo mismo vale por encima del umbral, p. ej. si preguntan por otro banco.
- Sobre las 30 preguntas de calibración: 8 caen en la zona gris (8 llamadas extra al LLM) y las decisiones correctas siguen siendo 27/30 ([bitácora M09](docs/modulos/M09.md#10-revisión-ajustes-y-cierre)).
- Se calibró con `eval/calibration.jsonl`: 15 preguntas que el sitio responde y 15 que no (fuera de dominio, otros bancos, prensa, simuladores). Acierta 27 de 30, **medido en la misma muestra con la que se eligió el umbral**: es un resultado dentro de la muestra y probablemente optimista. M13 lo valida con un golden set separado.
- Va sobre el score del reranker y **no sobre el coseno**: los cosenos de e5 están comprimidos (≈ 0,79–0,92 para todo). El mejor umbral posible sobre el coseno acierta 24 de 30 y queda pegado a los datos (margen 0,001).
- Sin reranker (`--no-rerank` o `RERANKER_ENABLED=false`) no se aplica umbral.
- **Latencia en CPU:** retrieval ~19 ms; rerank ~0,9 s (p50).
- Detalle y casos en la [bitácora M06](docs/modulos/M06.md#6-evidencia-manual).

**Generación con LLM (M7).** El LLM es **Gemini 2.5 Flash** ([ADR-012](docs/02_DECISIONES.md)), por su endpoint compatible con OpenAI. Grok (xAI) sigue disponible con `LLM_PROVIDER=xai`.
```bash
cp .env.example .env            # y completar GEMINI_API_KEY=... (clave gratuita de AI Studio)
python -m rag_bbva.cli llm-check   # lista los modelos de la clave y confirma LLM_MODEL (no gasta tokens)
```
- **Contexto y citas:** el modelo recibe solo el contexto recuperado (top-5 del reranker), numerado y delimitado, y debe citar cada afirmación con [n]. Las citas se convierten en URLs, y se descartan las inválidas y las repetidas.
- **Cuando no hay información:** si el umbral de M6 marca "sin información suficiente", **no se llama al LLM** y se responde: *"No encontré información suficiente en el sitio de Bancolombia…"*.
- **Otras entidades:** si preguntan por otro banco, el asistente aclara que solo tiene información de Bancolombia.
- **Inyección de prompts:** cualquier instrucción dentro del contenido scrapeado se trata como dato, no como orden.
- **Prompts versionados** en [`src/rag_bbva/llm/prompts.py`](src/rag_bbva/llm/prompts.py) (texto completo en la [bitácora M07](docs/modulos/M07.md#6-evidencia-manual)).
- **Reformulación de la pregunta** (`QUERY_REWRITE_MODE`): por defecto `history_only`, es decir, solo para preguntas de seguimiento. Medido en M7, reformular siempre (`always`) no mejoró la decisión del umbral, empeoró la URL esperada en el top-5 (13 → 11 de 15) y gasta una llamada más.
- **Reintentos:** un solo mecanismo propio ante 429, 5xx y timeouts (el SDK no reintenta). Los errores llegan al usuario como mensajes claros, sin trazas.
- **Costo:** con la clave gratuita el costo real es **$0**. El costo equivalente con los precios pagos de Google ($0,30 por millón de tokens de entrada y $2,50 de salida) fue **≈ $0,0014 por respuesta**: ~1550 tokens de entrada y ~360 de salida, medidos en 4 respuestas reales.
- **El LLM es un servicio externo:** Gemini con clave gratuita tiene límites de uso (L-11) y condiciones sobre los datos (L-12). Grok es de pago (ADR-003).
- **Si se agota el cupo diario de Gemini**, primero responde el modelo de respaldo (`LLM_FALLBACK_MODEL`, ADR-017). Si también se agota, el asistente lo dice sin reintentar. El cupo es **por proyecto y por modelo, no por clave**, así que una clave nueva del mismo proyecto no sirve. Para seguir:
  1. En https://aistudio.google.com/api-keys → *Create API key* → **en un proyecto nuevo**.
  2. Reemplazar `GEMINI_API_KEY=` en `.env`.
  3. Verificar con `python -m rag_bbva.cli llm-check`.
  - Alternativa sin clave nueva: el respaldo automático (`LLM_FALLBACK_MODEL=gemini-3.1-flash-lite`) o cambiar `LLM_MODEL` a otro modelo con cupo propio.
- **Las claves nunca se versionan:** solo van en `.env`, que está en `.gitignore`. El test `tests/unit/test_secrets.py` falla si algún archivo del repo contiene algo con forma de clave de Gemini o de xAI, o si `.env` dejara de estar ignorado.

**Memoria conversacional (M8).** El historial se guarda en SQLite (`HISTORY_DB_PATH`, por defecto `data/history/history.db`, fuera de git) y sobrevive a reinicios.
- Tablas `conversations` (id, fechas, título) y `messages` (rol, contenido, fuentes en JSON, latencias por etapa, score del reranker, `no_answer`, tokens y valoración 👍/👎). Las métricas alimentan la analítica de M11.
- `get_last_n(conversation_id, n)` devuelve los últimos `n` mensajes en orden cronológico; el servicio de M9 usará `n=HISTORY_WINDOW_N` (6) para reformular preguntas de seguimiento. `n=0` desactiva el contexto.
- **Contrato del ID** ([ADR-013](docs/02_DECISIONES.md)): el servidor crea la conversación y su ID (UUID4). Un ID que no existe **no se crea al vuelo**: se informa con un error (en la API será 404).
- Consulta de solo lectura desde la CLI:
```bash
python -m rag_bbva.cli history                     # conversaciones, la más reciente primero
python -m rag_bbva.cli history <ID> --last 6       # los últimos 6 mensajes (lo que vería el LLM)
```
- Lo escribe el servicio RAG de M9 (`POST /chat`), un turno completo a la vez (ADR-014).

**API REST (M9).** FastAPI sobre la fachada `RAGService`. Con Qdrant levantado y la clave en `.env`:
```bash
python -m rag_bbva.cli serve                 # http://127.0.0.1:8000 (API_HOST / API_PORT)
python -m rag_bbva.cli serve --port 8010     # si el 8000 está ocupado
```
Documentación interactiva (OpenAPI): **http://127.0.0.1:8000/docs**. Al arrancar se cargan el embedder y el reranker (~7,5 s en CPU), así la primera pregunta no paga esa espera.

| Método y ruta | Qué hace |
|---|---|
| `POST /chat` | `{question, conversation_id?}` → respuesta con citas. Sin `conversation_id` abre una conversación nueva (el servidor genera el UUID); con un ID inexistente, 404 |
| `GET /conversations?limit=50` | Conversaciones, la más reciente primero |
| `GET /conversations/{id}/messages` | Mensajes en orden, con fuentes, métricas y valoración |
| `POST /messages/{id}/feedback` | `{value: "up" \| "down"}` sobre una respuesta del asistente |
| `GET /health` | Estado de Qdrant, SQLite y la configuración del LLM, **sin gastar tokens**. 200 `ok` o 503 `degraded` |

```bash
# Primera pregunta: crea la conversación
curl -s -X POST http://127.0.0.1:8000/chat -H 'Content-Type: application/json' \
  -d '{"question": "¿Qué es el crédito de vivienda de Bancolombia?"}'
# Seguimiento: reenviar el conversation_id recibido
curl -s -X POST http://127.0.0.1:8000/chat -H 'Content-Type: application/json' \
  -d '{"question": "¿y cuáles son los requisitos?", "conversation_id": "<ID>"}'
curl -s http://127.0.0.1:8000/conversations/<ID>/messages
curl -s -X POST http://127.0.0.1:8000/messages/<MESSAGE_ID>/feedback -H 'Content-Type: application/json' -d '{"value": "up"}'
curl -s http://127.0.0.1:8000/health
```
Respuesta de `/chat` (abreviada):
```json
{"conversation_id": "948ede08-…", "message_id": 4, "question_message_id": 3,
 "answer": "Para solicitar un crédito de vivienda o un leasing habitacional… [1]",
 "sources": [{"n": 1, "url": "https://www.bancolombia.com/personas/creditos/vivienda/leasing-habitacional", "title": "Leasing habitacional para vivienda"}],
 "no_answer": false,
 "rewritten_query": "¿Cuáles son los requisitos para solicitar un crédito de vivienda o un leasing habitacional en Bancolombia?",
 "timings": {"rewrite": 746.5, "retrieval": 26.6, "rerank": 713.1, "llm": 1958.0, "total": 3453.7},
 "tokens": {"prompt": 2022, "completion": 393, "total": 2415},
 "model": "gemini-3.1-flash-lite", "prompt_version": "2026-10-03.1"}
```
- **Memoria:** cada pregunta usa los últimos `HISTORY_WINDOW_N` mensajes para reformularse (`rewritten_query`); el LLM recibe la pregunta original y la autónoma ([ADR-015](docs/02_DECISIONES.md)). La memoria vive en SQLite y sobrevive a reinicios del servidor.
- **Turno atómico** ([ADR-014](docs/02_DECISIONES.md)): la pregunta y la respuesta se guardan juntas al final. Si falla el LLM o Qdrant no se guarda nada, ni siquiera la conversación nueva.
- **Errores:** siempre JSON `{error, detail}`, sin trazas. 422 (pregunta vacía o de más de `CHAT_QUESTION_MAX_CHARS`=1000 caracteres), 404 (conversación o respuesta inexistente), 503 (LLM sin cupo o caído, Qdrant caído, historial no disponible, falta la clave) y 500 (inesperado).
- **Concurrencia:** endpoints síncronos que FastAPI ejecuta en su threadpool; SQLite con conexiones utilizables desde cualquier hilo, espera de 15 s ante bloqueos y modo WAL.
- **Latencia medida** (CPU, 5 turnos reales, M09.md §6): recuperación 18–54 ms, reranking 0,71–0,87 s, reformulación 0,75–1,1 s, LLM 1,1–2,0 s; total 1,9–3,5 s por turno.
- `/analytics` llega en M11.

- **Para M12:** dentro del contenedor la API escuchará en `API_HOST=0.0.0.0` (el aislamiento lo da Docker); en local sigue `127.0.0.1`. `API_PORT` es configurable porque el 8000 puede estar ocupado en la máquina.

**Docker.** Hoy existen la imagen base (`docker build .`; `docker compose run --rm api` ejecuta el comando `version`) y el servicio `qdrant` para desarrollo (`docker compose up -d qdrant`).
🚧 **El despliegue completo con `docker compose up -d --build` (Qdrant, API, UI) se completa en M12.** Ese arranque no scrapeará el sitio: usará un snapshot versionado de datos limpios.

---

## Uso de la interfaz conversacional

Interfaz web en **Streamlit** (M10) que habla con el sistema **solo por HTTP**, a través de la API de M9 (`ApiClient`); no importa el núcleo, y una prueba lo verifica.

**Levantarla** (Qdrant arriba y `GEMINI_API_KEY` en `.env`), en dos terminales:
```bash
python -m rag_bbva.cli serve                 # 1) API en http://127.0.0.1:8000
python -m rag_bbva.cli ui                    # 2) interfaz en http://127.0.0.1:8501
# Si el 8000 está ocupado:
python -m rag_bbva.cli serve --port 8010
python -m rag_bbva.cli ui --api-url http://127.0.0.1:8010
```
`UI_HOST`, `UI_PORT`, `API_BASE_URL` y `UI_REQUEST_TIMEOUT_SECONDS` (180 s) se configuran en `.env`.

**Qué ofrece**
- **Chat** con `st.chat_message` y un indicador "Buscando en el sitio de Bancolombia…" mientras responde.
- **Citas como enlaces:** cada `[n]` lleva a su página. Las fuentes (título + URL) van en un desplegable.
- **"Sin información suficiente"** con un aviso amarillo propio, tanto cuando el umbral corta como cuando el LLM se abstiene (ADR-016).
- **👍 / 👎** por respuesta (`POST /messages/{id}/feedback`); se deshabilitan después de votar, también al retomar una conversación ya valorada.
- **Barra lateral:**
  - nueva conversación y el `conversation_id` actual, visible y copiable;
  - lista para **retomar** conversaciones (título + fecha y hora UTC) y retomar **por ID**;
  - **estado del servicio** según `/health`: búsqueda (Qdrant), historial (SQLite) y LLM con su modelo de respaldo.
- **Modo detalle** (interruptor), para la demo: pregunta reformulada, zona gris, tiempos por etapa, tokens y modelo que respondió.
- **Errores amigables:** 503 (LLM sin cupo, Qdrant caído), 422 (pregunta vacía o de más de 1000 caracteres), 404 (ID inexistente: la siguiente pregunta abre una conversación nueva), timeout y API caída, que indica cómo levantarla. Una pregunta que falla no se guarda (turno atómico) y se cita en el error para reintentarla.
- **Aviso visible:** *"Prototipo de prueba técnica. No es un canal oficial de Bancolombia."* Sin logos ni marca: Bancolombia aparece solo como fuente.

**Capturas** (guion completo en la [bitácora M10](docs/modulos/M10.md#6-evidencia-manual)):

| | |
|---|---|
| ![Pantalla inicial](docs/img/m10_01_inicio.png) Pantalla inicial: aviso, estado del servicio y conversaciones para retomar | ![Respuesta con citas](docs/img/m10_02_citas_y_fuentes.png) Seguimiento "¿y cuáles son los requisitos?" con citas enlazadas y fuentes |
| ![Modo detalle](docs/img/m10_03_modo_detalle.png) Modo detalle en la zona gris, con 👍 ya votado | ![Sin información](docs/img/m10_04_sin_informacion.png) Pregunta fuera de dominio: "sin información suficiente" |
| ![Otra entidad](docs/img/m10_05_otra_entidad.png) Pregunta sobre otro banco: el asistente se abstiene | ![Retomar por ID](docs/img/m10_06_retomar_por_id.png) Conversación retomada por ID después de reiniciar la API |
| ![API caída](docs/img/m10_07_api_caida.png) API detenida: estado en rojo y error amigable | |

**CLI de respaldo**, sin API ni navegador, directo sobre `RAGService`:
```bash
python -m rag_bbva.cli chat                          # conversación nueva
python -m rag_bbva.cli chat --conversation-id <ID>   # continuar una existente
```
Muestra la respuesta, las fuentes y, al salir (`salir` o línea vacía), el ID de la conversación.

---

## Patrones de diseño

El caso exige al menos 3. Previstos en la [visión general §6](docs/00_VISION_GENERAL.md):

| Patrón | Dónde | Por qué | Estado |
|---|---|---|---|
| **Singleton (vía caché)** | [`src/rag_bbva/config.py`](src/rag_bbva/config.py): `get_settings()` con `lru_cache` | La configuración se lee y valida una sola vez; todo el código la obtiene del mismo punto | ✅ M0 |
| **Strategy** (inyección de dependencias) | [`src/rag_bbva/scraping/exploration.py`](src/rag_bbva/scraping/exploration.py): `Renderer` (`Protocol`); [`src/rag_bbva/scraping/storage.py`](src/rag_bbva/scraping/storage.py): función de huella inyectable en `RawStorage` | El explorador funciona con Playwright, con un doble o sin renderizador. El almacenamiento detecta cambios con la huella que se le inyecte (bytes por defecto, texto visible en el crawler) sin cambiar su código | ✅ parcial (M1–M2) |
| **Strategy** (algoritmos intercambiables) | [`src/rag_bbva/indexing/chunking.py`](src/rag_bbva/indexing/chunking.py): `ChunkingStrategy` → `HeadingAwareChunker` / `FixedSizeChunker`; [`src/rag_bbva/indexing/embedding.py`](src/rag_bbva/indexing/embedding.py): `Embedder` → `SentenceTransformerEmbedder` / `FakeEmbedder` | Cambiar cómo se trocea o cómo se embebe sin tocar el pipeline: la línea base de chunking se compara con la principal y los tests usan un embedder falso, sin modelo | ✅ M4 |
| **Strategy** (reranking) | [`src/rag_bbva/retrieval/reranker.py`](src/rag_bbva/retrieval/reranker.py): `Reranker` → `CrossEncoderReranker` / `NoOpReranker`; la fábrica elige según `RERANKER_ENABLED` | Activar o desactivar el reranker sin tocar el `Retriever`; los tests usan un reranker determinista | ✅ M6 |
| **Strategy** (proveedor de LLM) | [`src/rag_bbva/llm/provider.py`](src/rag_bbva/llm/provider.py): `LLMProvider` → `OpenAICompatibleProvider` (`GeminiProvider`, `XaiGrokProvider`) / `FakeLLMProvider` | Cambiar de Grok a Gemini fue solo configuración (`LLM_PROVIDER`). Los tests usan un LLM falso y no gastan cupo ni créditos | ✅ M7 |
| **Decorator** (respaldo del LLM) | [`src/rag_bbva/llm/provider.py`](src/rag_bbva/llm/provider.py): `FallbackLLMProvider(primary, fallback)`, que implementa `LLMProvider` y envuelve a otro `LLMProvider`; la fábrica lo aplica si hay `LLM_FALLBACK_MODEL` | Agregar el respaldo ante cupo agotado sin tocar `GeminiProvider` ni a quienes usan el LLM (reformulador, generador): ven la misma interfaz. Solo reacciona a `LLMQuotaError` ([ADR-017](docs/02_DECISIONES.md)) | ✅ M9 |
| **Template Method** | [`src/rag_bbva/scraping/base.py`](src/rag_bbva/scraping/base.py): `BaseCrawler.crawl()`; subclase concreta [`SitemapBfsCrawler`](src/rag_bbva/scraping/crawler.py) | `crawl()` fija el algoritmo (`prepare` → `discover_urls` → `fetch` → `validate` → `persist` → `extract_links`) y aplica en un solo lugar los límites, la deduplicación y el corte por bloqueo. Las subclases solo redefinen los pasos | ✅ M2 |
| **Chain of Responsibility / Pipeline** | [`src/rag_bbva/processing/steps.py`](src/rag_bbva/processing/steps.py): `CleaningStep` (`set_next`/`handle`) y sus pasos; [`src/rag_bbva/processing/pipeline.py`](src/rag_bbva/processing/pipeline.py): `CleaningPipeline` | Cada paso de la limpieza (parseo, metadatos, boilerplate, extracción, normalización, idioma, longitud, duplicados) es una clase que transforma el documento y lo pasa al siguiente, o corta la cadena con el motivo del descarte. Se prueban por separado y se pueden reordenar o sustituir | ✅ M3 |
| **Factory** | [`src/rag_bbva/indexing/factory.py`](src/rag_bbva/indexing/factory.py): `ComponentFactory` (`create_chunker`, `create_embedder`, `create_vector_store`, `create_reranker`, `create_retriever`, `create_llm`…) | Crear cada componente desde la configuración (`CHUNKING_STRATEGY`, `EMBEDDING_PROVIDER`, `RERANKER_ENABLED`, `LLM_PROVIDER`…) sin acoplar el resto del código a clases concretas. Si falta la clave del LLM, falla al crearlo con un error claro | ✅ M4–M7 |
| **Adapter** (puerto de la base vectorial) | [`src/rag_bbva/indexing/vector_store.py`](src/rag_bbva/indexing/vector_store.py): interfaz `VectorStore` → `QdrantVectorStore` | La ingesta (y la recuperación de M6) hablan con una interfaz propia: `ensure_collection`, `upsert`, `search` con filtro por sección, `count`, `delete`. El adaptador traduce a `qdrant-client` y sus errores a `IndexingError`. Los tests usan el mismo adaptador sobre `QdrantClient(":memory:")` | ✅ M5 |
| **Repository** | [`src/rag_bbva/memory/repository.py`](src/rag_bbva/memory/repository.py): interfaz `ConversationRepository` → [`SqlAlchemyConversationRepository`](src/rag_bbva/memory/sql_repository.py) (SQLite) / `InMemoryConversationRepository` | El servicio pide "los últimos N mensajes" o "guarda este mensaje" sin saber de SQL. Las mismas pruebas de contrato corren contra las tres variantes (memoria, SQLite en memoria y en archivo) | ✅ M8 |
| **Facade** | [`src/rag_bbva/services/rag_service.py`](src/rag_bbva/services/rag_service.py): `RAGService.ask(conversation_id, question)` | Un único punto de entrada que orquesta historial → reformulación → recuperación → reranking → umbral → generación → guardado atómico. La API (y la UI de M10) solo hablan con la fachada; los tests la arman con dobles | ✅ M9 |
| **Inyección de dependencias** (app factory) | [`src/rag_bbva/api/app.py`](src/rag_bbva/api/app.py): `create_app(settings, service=…, health_checker=…)` y `Depends(get_rag_service)` | La app recibe sus componentes en lugar de crearlos por dentro: en producción los arma el `lifespan` con la fábrica; en los tests se pasan dobles o se usa `app.dependency_overrides` | ✅ M9 |

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
| LLM | **Gemini 2.5 Flash** vía SDK `openai` (endpoint compatible); Grok (xAI) como alternativa | Calidad en español sin GPU local; clave **gratuita** de AI Studio (costo real $0) y aislado tras `LLMProvider` ([ADR-012](docs/02_DECISIONES.md), [ADR-003](docs/02_DECISIONES.md)) | ✅ en uso (M7) |
| Orquestación RAG | Código propio, sin LangChain | Patrones visibles y testeables ([ADR-001](docs/02_DECISIONES.md)) | ✅ en uso (M9) |
| Historial | SQLite + SQLAlchemy 2 | Cero infraestructura extra, persistente ([ADR-004](docs/02_DECISIONES.md)) | ✅ en uso (M8) |
| API | FastAPI + uvicorn | Validación con Pydantic, OpenAPI automático (`/docs`), `TestClient` | ✅ en uso (M9) |
| UI | Streamlit | Chat en pocas líneas y testeable sin navegador (`AppTest`); consume la API por HTTP | ✅ en uso (M10) |
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
- **Gemini en lugar de Grok:** la API de xAI se quedó sin créditos; Gemini 2.5 Flash con clave gratuita, por el mismo SDK (ADR-012).
- **Umbral doble:** debajo de −3,0 no se llama al LLM; en la zona gris el LLM decide y se abstiene con `[SIN_INFO]` ([ADR-016](docs/02_DECISIONES.md)).
- **Modelo de respaldo solo ante cupo agotado** (Decorator, [ADR-017](docs/02_DECISIONES.md)).
- **Turno atómico:** pregunta y respuesta se guardan juntas o ninguna, para que un fallo del LLM no deje preguntas sueltas en el contexto ([ADR-014](docs/02_DECISIONES.md)).
- **La respuesta de un seguimiento usa también la pregunta autónoma** reformulada con el historial ([ADR-015](docs/02_DECISIONES.md)).
- **Un `conversation_id` desconocido se informa, no se crea:** evita continuar sin contexto una conversación mal copiada ([ADR-013](docs/02_DECISIONES.md)).
- **Claves del LLM opcionales** al cargar la configuración; se exigen al crear el proveedor (ADR-006).
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
| L-11 | **Cupo del nivel gratuito de Gemini:** la clave gratuita permite **20 solicitudes por día** a `gemini-2.5-flash` en este proyecto (lo informa el propio error 429). Al agotarse, el asistente responde con un aviso claro, sin reintentar, hasta el reinicio diario (medianoche del Pacífico) o hasta que se use una clave de **otro proyecto** (el cupo es por proyecto, no por clave). Las preguntas que el umbral corta no consumen cupo. **Modelo de respaldo automático** ([ADR-017](docs/02_DECISIONES.md)): como el cupo es por modelo, ante un 429 del principal la misma llamada se repite con `LLM_FALLBACK_MODEL` (por defecto `gemini-3.1-flash-lite`), y la respuesta de la API indica qué modelo respondió (`model`). Verificado en M9: con el cupo de `gemini-2.5-flash` agotado, el respaldo respondió 20 de 22 preguntas y los 5 turnos de una conversación. Solo se activa ante cupo o límite (429), no ante errores de clave o de modelo. Si el respaldo también agota su cupo, se informa el mismo 503. La calidad del respaldo no se ha medido por separado (M13). Para uso real o evaluaciones grandes hace falta el nivel pago | M7, ADR-012 |
| L-12 | **Datos en el nivel gratuito de Gemini:** según los términos de la Gemini API, en los servicios sin pago Google puede usar prompts y respuestas para mejorar sus productos y pueden revisarlos personas (*"Do not submit sensitive, confidential, or personal information"*). El contexto es contenido público de Bancolombia, pero **las preguntas no deben incluir información sensible o personal**. El nivel pago no usa los datos para mejorar productos | M7, ADR-012 |
| L-13 | **Historial en un solo archivo SQLite:** pensado para una instancia de la API y pocos usuarios a la vez; SQLite serializa las escrituras. Tampoco hay migraciones de esquema: las tablas se crean si faltan, pero un cambio de columnas en el futuro exigiría migrar (p. ej. con Alembic) o recrear `history.db`. Las preguntas quedan guardadas en texto plano en el volumen de datos | M8, ADR-004 |
| L-14 | **API sin autenticación ni límite de uso:** pensada para usuarios internos en una red de confianza (S-05). Cualquiera que alcance el puerto puede preguntar, gastar cupo del LLM y leer todas las conversaciones con `GET /conversations`. Por eso `API_HOST` es `127.0.0.1` por defecto. No hay streaming en la API: la respuesta llega completa (el generador de M7 ya lo soporta; la UI de M10 decidirá si lo expone) | M9, S-05 |
| L-15 | **Interfaz sin streaming ni sesiones de usuario:** la respuesta aparece completa cuando termina (indicador de espera de hasta `UI_REQUEST_TIMEOUT_SECONDS`=180 s; con el LLM gratuito saturado, un turno real tardó ~150 s en M9). La lista de conversaciones muestra las de todos (no hay usuarios, L-14) y las horas van en UTC | M10 |

---

## Futuras mejoras

Ideas registradas en las decisiones; ninguna está implementada:

- Proveedor LLM open source local (p. ej. vía Ollama), implementando otra estrategia de `LLMProvider`. Pasar Gemini al nivel pago para más cupo y para que Google no use los datos (L-11, L-12).
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
│   ├── llm/               # provider (Strategy: Gemini, Grok, Fake), prompts, citations, rewriter, generator
│   ├── memory/            # models, repository (Repository: interfaz + memoria), sql_repository (SQLite)
│   ├── services/          # rag_service (Facade), health (estado sin gastar tokens)
│   ├── api/               # app (create_app, rutas, lifespan), schemas, errors (JSON {error, detail})
│   ├── ui/                # app (Streamlit), api_client (HTTP de la API), render (presentación)
│   └── analytics/         # vacío (M11)
├── scripts/explore_site.py · scripts/trim_html_fixture.py · scripts/calibrate_reranker.py · scripts/llm_evidence.py
├── tests/unit/ · tests/fixtures/ (robots, sitemaps, html: páginas reales recortadas de las 3 plantillas; clean: glosario limpio) · tests/integration/
├── eval/                  # calibration.jsonl (M6: umbral del reranker); golden set (M13)
└── docs/
```

Documentación:
- [Visión general](docs/00_VISION_GENERAL.md): requisitos, arquitectura, configuración, supuestos.
- [Plan de módulos](docs/01_PLAN_DE_MODULOS.md): tareas, pruebas de aceptación y Definition of Done.
- [Decisiones (ADR)](docs/02_DECISIONES.md).
- [Exploración del sitio](docs/exploracion_sitio.md) y su [evidencia JSON](docs/evidencia/).
- Bitácoras por módulo: [M00](docs/modulos/M00.md) · [M01](docs/modulos/M01.md) · [M02](docs/modulos/M02.md) · [M03](docs/modulos/M03.md) · [M04](docs/modulos/M04.md) · [M05](docs/modulos/M05.md) · [M06](docs/modulos/M06.md) · [M07](docs/modulos/M07.md).
- [CHANGELOG](CHANGELOG.md).
