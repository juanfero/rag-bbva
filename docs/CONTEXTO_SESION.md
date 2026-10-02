# Contexto de sesión — traspaso

> Escrito el 2026-10-02 para retomar el proyecto en una sesión nueva de Claude Code sin el historial de la conversación anterior. Solo contiene hechos verificables en el repo; no incluye secretos.
> Si este archivo contradice al código o a `git log`, manda el repo: verifica con los comandos de §5.

---

## 1. Estado actual

### Módulos cerrados (merge a `main` + tag publicado)
| Módulo | Tag | Commit del tag (merge) | Bitácora |
|---|---|---|---|
| M0 — Fundaciones | `m00` | `bc14425` | `docs/modulos/M00.md` |
| M1 — Exploración del sitio | `m01` | `f48a0d3` | `docs/modulos/M01.md` |
| M2 — Scraper (datos crudos) | `m02` | `4a31943` | `docs/modulos/M02.md` |
| M3 — Limpieza (datos limpios) | `m03` | `731c389` | `docs/modulos/M03.md` |
| M4 — Chunking + embeddings | `m04` | merge `--no-ff` de `feat/m04-chunking-embeddings` en `main` (2026-10-02); el hash se ve con `git rev-parse --short m04^{commit}` | `docs/modulos/M04.md` |

- Commits `docs` directos en `main`, pedidos de forma explícita por Juan Felipe:
  - `5edf0dd`, entre `m00` y `m01`.
  - `c38abdc` (README inicial) y `9a6e3ce` (regla del README incremental), entre `m01` y `m02`.
- Los merges de M2, M3 y M4 incluyen las correcciones de cada revisión (§10 de cada bitácora). M4 incluye un ajuste a la limpieza de M3: quitar bloques de venta cruzada y "Link copiado", y trafilatura solo si conserva el orden.

### Siguiente módulo: M5 — Indexación vectorial (Qdrant)
- Juan Felipe pidió empezarlo junto con el cierre de M4, en la rama `feat/m05-qdrant`.
- Pedido, resumido; el texto completo está en la conversación y en `M05.md` cuando exista:
  - Servicio `qdrant` en `docker-compose.yml`: imagen con versión fija, volumen `qdrant_data` y healthcheck. `qdrant-client` compatible con esa imagen.
  - `VectorStore` → `QdrantVectorStore`: colección coseno de 384 dimensiones, upsert por lotes con payload, búsqueda top-k con filtro por `section`, índice de payload, count y borrado por ids. Error de conexión → `IndexingError`.
  - Comando `ingest`:
    - ids `uuid5(chunk_id)`;
    - sincronización completa, que borra los puntos obsoletos;
    - caché de embeddings por hash del texto en `data/embeddings/`;
    - `--recreate`;
    - reporte por etapa.
  - Tests con `QdrantClient(":memory:")`; el del Qdrant real va marcado `integration`.
  - Evidencia:
    - ingesta real con conteo y tiempo;
    - re-ingesta sin cambios (0 embebidos);
    - las 5 preguntas vía Qdrant iguales a la fuerza bruta;
    - filtro por sección;
    - tamaños en disco.
  - **Sin merge.**

### Estado del árbol (al cerrar M4)
- `main` con el merge de M4 y el tag `m04`, publicados en `origin`. Las ramas `feat/m00…m04` siguen a sus pares en `origin`.
- Solo en local, ignorado por git: `.venv/`, `models/` y `data/`.
  - `models/`: caché de Hugging Face con `intfloat/multilingual-e5-small` (471 MB).
  - `data/raw/`: crawl completo del 2026-10-02 (manifest de 1275 entradas, 687 HTML).
  - `data/clean/`: 597 documentos, tras el ajuste de M4.
  - `data/chunks/`: 3506 chunks `heading_aware` y `chunk_report.json`.
  - Logs de los crawls en `data/`.
  - No hay `.env`; ningún comando lo necesita hasta M7.

---

## 2. Último pedido de Juan Felipe y hasta dónde se llegó

Revisión de M4: **aprobado con un ajuste**, ya aplicado:
- bloques de venta cruzada y "Link copiado" fuera de la limpieza, con tests y chequeo de fugas;
- `clean` + `chunk` + las 5 preguntas vueltas a correr: "¿dónde hay cajeros?" ahora devuelve `cajeros` y `puntos-de-atencion`;
- tests `slow` que se saltan sin el modelo en la caché local;
- nota para M6 sobre el umbral sobre el score del reranker.

Se cerró M4 (bitácora ✅, CHANGELOG `[m04]`, README ✅, merge `--no-ff`, tag `m04` y push). A continuación se empieza M5 (§1).

---

## 3. Decisiones y temas abiertos que NO están en los ADR, en `00_VISION_GENERAL.md §9` ni en `CLAUDE.md`

### Preguntas abiertas
- Ninguna al cerrar M3.

### Decisiones de implementación de M2, M3 y M4
Están en `M02.md §4`, `M03.md §4`, `M04.md §4` y en la sección "Decisiones" del README, **no** en ADR:
- **M2:**
  - semillas intercaladas por sección;
  - detección incremental por huella del texto visible;
  - deduplicación por URL final;
  - `BlockGuard` corta solo con 5 respuestas 403/429 seguidas;
  - fragmento de cuerpos de error ≤ 1 KB;
  - `attempts` y `elapsed_ms` medidos sobre todas las peticiones de una descarga;
  - BFS FIFO.
- **M3:**
  - el boilerplate se quita del DOM antes de extraer;
  - trafilatura solo si conserva ≥ 90 % del vocabulario del contenedor, si no *fallback* por selector;
  - `html_lang` (valor declarado) separado de `lang` (detectado por palabras funcionales, sin dependencias); la plantilla C declara `en` en páginas en español;
  - bloques repetidos de ≥ 60 caracteres se conservan una vez;
  - el manifest se escribe de forma incremental y Ctrl+C/SIGTERM cierran el crawl con `interrumpido` (exit 130);
  - el patrón de fuga `pie` solo cuenta las frases tal como están en el pie.
- **M4:**
  - `CHUNK_SIZE` en caracteres, verificado en tokens con el tokenizer real (máximo 422 de 512);
  - HeadingAware agrupa secciones pequeñas bajo su ruta común y es la estrategia por defecto;
  - encabezado de contexto (título | sección + `heading_path`) solo en `embedding_text`;
  - torch CPU-only se instala antes desde el índice de PyTorch;
  - con el modelo en caché se carga con `local_files_only`;
  - los scores coseno de e5 están comprimidos (≈ 0,83–0,91): el umbral de "sin información" va sobre el reranker (M6).

### Prácticas acordadas en la conversación
No están escritas en `CLAUDE.md`; la forma de trabajo de §4 las recoge:
- Se hacen commits directos en `main` **solo** cuando Juan Felipe lo pide explícitamente; hasta ahora, únicamente commits `docs`.
- Las ramas de módulo también se publican en `origin`, no solo `main` y los tags.
- `git commit --amend` solo para commits **no publicados**. El historial publicado no se reescribe.
- Al pedir evidencia de corridas reales, se afirma solo lo que se puede verificar con archivos. Si un reporte se sobrescribió, se dice.
- **Corridas largas contra el sitio** (crawl completo): se avisa antes y se da el comando para que Juan Felipe lo corra en otra terminal, o se lanza en segundo plano si lo pide. **No se cambia el código** del directorio mientras corre: los cambios se hacen en un worktree aparte y se integran al terminar.
- Ante hallazgos en datos reales (fugas, idioma, redirecciones), primero se revisan los casos y se clasifican como reales o falsos positivos, y luego se corrige con test.

Todo lo demás está registrado:
- ADR-001 a 011.
- Supuestos S-01 a S-08 (S-02, S-03 y S-04 confirmados; S-03 acotado por ADR-010 y ampliado por ADR-011).
- Limitaciones L-01 a L-10 en el README.
- Reglas en `CLAUDE.md`.

---

## 4. Forma de trabajo

- **Un módulo a la vez** (M0 → M14), sin adelantar código de módulos futuros.
- Cada módulo:
  - Rama `feat/mXX-nombre` y bitácora `docs/modulos/MXX.md`, copiada de `_PLANTILLA.md`.
  - Commits pequeños en Conventional Commits en español.
  - Al final, la Definition of Done de `docs/01_PLAN_DE_MODULOS.md` (pytest, ruff, bitácora, CHANGELOG, ADR, README).
- **Revisión externa:** Juan Felipe pega las salidas de esta sesión en otro chat, donde un revisor las valida. Por eso cada entrega debe ser autocontenida y verificable.
- **No se hace merge sin aprobación explícita** de Juan Felipe. Al terminar un módulo se le muestra:
  - salida de `pytest`;
  - salida de `ruff check . && ruff format --check .`;
  - `git log --oneline --graph`;
  - un resumen de lo hecho, con evidencia real cuando aplica, y lo que pida en particular (p. ej. el diff del README).
- **Las dudas y ambigüedades se preguntan antes de implementar.** Si se decide un supuesto, se registra en `00_VISION_GENERAL.md §9` y en `docs/02_DECISIONES.md`.
- **Si un test falla:** se corrige el código, nunca se debilita la prueba. Si la expectativa de un test recién escrito era errónea, se explica en la bitácora.
- Solo se escriben hechos verificables en README, bitácoras y docs.

---

## 5. Entorno

- **Ruta del proyecto:** `/home/pipe/Inetum/rag-bbva-docs/rag-bbva`
- **Python:** 3.11.17, instalado con `uv` 0.12.22, en `.venv/`.
  - Activar con `source .venv/bin/activate`.
  - El `.venv` **no trae `pip`**: instalar con `uv pip install --python .venv/bin/python -e ".[dev]"`.
- **Remote:** `git@github.com:juanfero/rag-bbva.git`. El repo es público; también se puede clonar con `https://github.com/juanfero/rag-bbva.git`.
- **Docker:** Docker 29.8.1 y Compose v5.5.1. La imagen base construye con `docker build .`.
- **Caché de Playwright:** `~/.cache/ms-playwright` (≈ 658 MB, Chromium y ffmpeg) quedó de la medición de M1. Playwright **no** está instalado en el `.venv` ni es dependencia (ADR-009). Se puede borrar si no se va a repetir `scripts/explore_site.py --render`.

Comandos de verificación:
```bash
cd /home/pipe/Inetum/rag-bbva-docs/rag-bbva
source .venv/bin/activate
git status && git branch -vv && git log --oneline --graph --decorate -15 && git tag
pytest                                   # al cerrar M4: 319 passed (los 3 slow requieren el modelo en models/; sin él se saltan)
pytest -m "not integration and not slow"
ruff check . && ruff format --check .
python -m rag_bbva.cli version
python -m rag_bbva.cli scrape --max-pages 50   # red real: ~77 s contra www.bancolombia.com
python -m rag_bbva.cli scrape --max-pages 1200 # crawl completo: ~28,5 min (correr en otra terminal)
python -m rag_bbva.cli clean                   # ~51 s sobre el crawl completo
python -m rag_bbva.cli chunk                   # ~10 s (carga el tokenizer del modelo)
docker build -t rag-bbva:latest .
```

---

## 6. Pendientes asignados a módulos futuros y deuda técnica

Fuentes: `docs/01_PLAN_DE_MODULOS.md`, las bitácoras §8 y el README.

- **M5 — Qdrant:** ver §1. Los filtros por sección deben aceptar secciones fuera de las 6 principales (ADR-011). `chunk_id` (SHA-1) → id de punto con `uuid5`.
- **M6 — Reranker:** umbral de "sin información suficiente" sobre el score del reranker, no sobre el coseno de e5.
- **M7 — LLM:**
  - Exigir `XAI_API_KEY` al crear `XaiGrokProvider`, con error claro (ADR-006).
  - Verificar que `LLM_MODEL` exista con `GET /v1/models`.
- **M12 — Docker:**
  - Montar `MODEL_CACHE_DIR` como volumen. Embeber ~3500 chunks toma ~2,6 min en CPU: indexar solo si la colección está vacía.
  - Instalar torch desde el índice CPU de PyTorch antes del proyecto.
  - `docker compose up` **no scrapea**: se versiona un snapshot de `data/clean/`, `init` solo indexa si la colección está vacía y el scraping completo es un comando opcional.
  - Volúmenes de `data/` con permisos para el usuario no root (uid 1000). En M0 se quitó el volumen `./data` porque Docker lo creaba como root.
- **M14:**
  - Pulido final del README, manteniendo la nota inicial sobre la fuente Bancolombia con el enlace a ADR-008.
  - Verificación desde cero en una carpeta limpia, siguiendo el README literalmente.
  - Revisión del historial y tag `v1.0.0`.
- **Riesgo operativo:** el sitio carga un recurso de **Incapsula** (bot-manager). A 1 req/s no ha bloqueado: el crawl completo de M3 (1694 peticiones) terminó sin un solo 403/429 de bloqueo. Si se activara, `BlockGuard` corta y el manifest incremental conserva lo avanzado.
- **Mejora futura (L-10):** que el cupo de `--max-pages` cuente solo los HTML únicos guardados.

---

## 7. Lo que la próxima sesión NO debe romper

- **Los tags publicados no se mueven ni se reescriben:** `m00` → `bc14425`, `m01` → `f48a0d3`, `m02` → `4a31943`, `m03` → `731c389`, `m04` → merge de M4. Tampoco se reescribe historial ya publicado en `origin`: nada de `push --force` ni rebase de ramas publicadas.
- **Nunca escribir la `XAI_API_KEY`** en código, docs, tests ni commits; solo en `.env`, que está en `.gitignore`.
- **Bancolombia en todo texto visible al usuario** (prompts, UI, respuestas, README, ayuda de la CLI). El código conserva `rag_bbva`. Un test de `tests/unit/test_cli.py` verifica que la ayuda de la CLI diga Bancolombia y no BBVA.
- **Ningún módulo se mergea sin la aprobación explícita** de Juan Felipe; M5 tampoco.
- **Cortesía con el sitio:** respetar `robots.txt`, User-Agent `RAG-BBVA-TechTest/1.0`, pausa ≥ 1 s, sin seguir redirecciones a otros dominios y sin eludir el WAF o el bot-manager.
- `data/`, `models/` y `.env` no se versionan.

---

## 8. Orden de lectura recomendado

1. `docs/CONTEXTO_SESION.md` (este archivo).
2. `CLAUDE.md`: reglas obligatorias.
3. `README.md`: estado, uso, patrones y limitaciones L-01 a L-10.
4. `docs/00_VISION_GENERAL.md`: requisitos, arquitectura, configuración §7 y supuestos §9.
5. `docs/01_PLAN_DE_MODULOS.md`: Definition of Done y el módulo en curso o siguiente.
6. `docs/02_DECISIONES.md`: ADR-001 a ADR-011.
7. `docs/modulos/M04.md` (último cerrado; §8 tiene los pendientes) y `M05.md` si existe; luego `M03.md`, `M02.md`, `M01.md` y `M00.md` si hace falta.
8. `docs/exploracion_sitio.md`: hallazgos del sitio, selectores y riesgos.
9. `CHANGELOG.md`.
