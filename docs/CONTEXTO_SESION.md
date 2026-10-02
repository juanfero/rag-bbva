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
| M4 — Chunking + embeddings | `m04` | `777402b` | `docs/modulos/M04.md` |
| M5 — Indexación vectorial (Qdrant) | `m05` | `a47323a` | `docs/modulos/M05.md` |
| M6 — Recuperación + reranker | `m06` | merge `--no-ff` de `feat/m06-retrieval` en `main` (2026-10-02); el hash se ve con `git rev-parse --short m06^{commit}` | `docs/modulos/M06.md` |

- Commits `docs` directos en `main`, pedidos de forma explícita por Juan Felipe:
  - `5edf0dd`, entre `m00` y `m01`.
  - `c38abdc` y `9a6e3ce`, entre `m01` y `m02`.
- Cada merge incluye las correcciones de su revisión (§10 de cada bitácora).

### Siguiente módulo: M7 — Generación con LLM
- Juan Felipe pidió empezarlo junto con el cierre de M6, en la rama `feat/m07-llm`. Ya creó `.env` con `XAI_API_KEY`: **no leerla ni imprimirla**; solo verificar que exista y que `.env` esté ignorado.
- Pedido, resumido; el texto completo está en la conversación y en `M07.md` cuando exista:
  - **`LLMProvider` (Strategy):**
    - `XaiGrokProvider` con el SDK `openai` (`base_url=XAI_BASE_URL`), timeout y reintentos solo en 429/5xx/timeouts (un solo mecanismo, documentado), streaming y registro de tokens;
    - `FakeLLMProvider`;
    - sin clave → `ConfigurationError`;
    - comando `llm-check`: si `LLM_MODEL` no existe, mostrar la lista y **preguntar antes de cambiarlo**.
  - **Prompts versionados en español:**
    - solo contexto, citas [n], no inventar;
    - aclarar si preguntan por otra entidad;
    - contexto delimitado con defensa contra inyección;
    - `QueryRewriter` (pregunta autónoma + expansión de siglas, p. ej. "4 por mil" → GMF), con `QUERY_REWRITE_MODE=off|history_only|always`.
  - **`no_answer`** del umbral de M6 → respuesta fija, sin llamar al LLM.
  - **Post-proceso de citas:** [n] → URL, índices inválidos fuera, deduplicadas.
  - **Tests** con respx, sin gastar créditos; el real contra Grok como `integration`, que se salta sin clave.
  - **Evidencia** (pocas llamadas reales):
    - `llm-check`;
    - 6 preguntas de punta a punta con tokens, latencias y costo según el precio **oficial** de xAI citado;
    - experimento `off` frente a `always` sobre `eval/calibration.jsonl`;
    - system prompt completo en M07.md.
  - **Sin merge.**

### Estado del árbol (al cerrar M6)
- `main` con el merge de M6 y el tag `m06`, publicados en `origin`. Las ramas `feat/m00…m06` siguen a sus pares en `origin`.
- **Qdrant del compose levantado** (`docker compose up -d qdrant`), volumen `qdrant_data`, colección `bancolombia_docs` con 3506 puntos.
- Solo en local, ignorado por git: `.venv/`, `.env` (con `XAI_API_KEY`, creado por Juan Felipe), `models/` (e5-small y cross-encoder, 936 MB) y `data/`.
  - `data/raw/`: crawl completo.
  - `data/clean/`: 597 documentos.
  - `data/chunks/`: 3506 chunks.
  - `data/embeddings/`: caché de 6 MB.
  - `data/eval/calibration_report.json`: última corrida de la calibración de M6.

---

## 2. Último pedido de Juan Felipe y hasta dónde se llegó

Revisión de M6: **aprobado**. Antes del cierre:
- se declaró que el 27/30 del umbral es un resultado dentro de la muestra (M13 lo valida aparte);
- se anotaron para M7 Banco de Bogotá (prompt) y "4 por mil" (reformulación).

Se cerró M6 (bitácora ✅, CHANGELOG `[m06]`, README ✅, merge `--no-ff`, tag `m06` y push). A continuación se empieza M7 (§1).

---

## 3. Decisiones y temas abiertos que NO están en los ADR, en `00_VISION_GENERAL.md §9` ni en `CLAUDE.md`

### Preguntas abiertas
- Ninguna al cerrar M3.

### Decisiones de implementación de M2 a M6
Están en `M02.md §4` a `M06.md §4` y en la sección "Decisiones" del README, **no** en ADR:
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
- **M5:**
  - `QDRANT_URL` por defecto `http://localhost:6333` (dentro de Docker, `http://qdrant:6333`);
  - cliente con `check_compatibility=False` (versiones fijadas);
  - sincronización por hash de `embedding_text`;
  - la caché de embeddings no se poda;
  - puerto de Qdrant solo en `127.0.0.1`;
  - con 3506 vectores Qdrant busca de forma exacta (sin HNSW: `indexed_vectors_count = 0`).
- **M6:**
  - umbral `RERANK_MIN_SCORE=1.6` sobre el logit del cross-encoder (27/30 dentro de la muestra; el coseno, 24/30 con margen 0,001);
  - sin reranker no hay umbral;
  - diversidad de 2 chunks por página;
  - top-k 20 (el chunk de requisitos de vivienda venía del puesto 20);
  - `warm_up()` excluye la carga de modelos de las latencias.

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
pytest                                   # al cerrar M6: 358 passed (slow: requieren el modelo en models/; integration: Qdrant levantado; si no, se saltan)
pytest -m "not integration and not slow"
ruff check . && ruff format --check .
python -m rag_bbva.cli version
python -m rag_bbva.cli scrape --max-pages 50   # red real: ~77 s contra www.bancolombia.com
python -m rag_bbva.cli scrape --max-pages 1200 # crawl completo: ~28,5 min (correr en otra terminal)
python -m rag_bbva.cli clean                   # ~51 s sobre el crawl completo
python -m rag_bbva.cli chunk                   # ~10 s (carga el tokenizer del modelo)
docker compose up -d qdrant
python -m rag_bbva.cli ingest                  # ~2,5 min sin caché; 0,2 s si no hay cambios; --recreate con caché: 1,6 s
python -m rag_bbva.cli search "¿qué es un CDT?" # retrieval ~19 ms + rerank ~0,9 s en CPU
python scripts/calibrate_reranker.py           # recalibra el umbral con eval/calibration.jsonl
docker build -t rag-bbva:latest .
```

---

## 6. Pendientes asignados a módulos futuros y deuda técnica

Fuentes: `docs/01_PLAN_DE_MODULOS.md`, las bitácoras §8 y el README.

- **M7 — LLM:** ver §1. Casos que vienen de M6: Banco de Bogotá (prompt) y "4 por mil" (reformulación).
- **M11:** guardar `retrieval_ms`, `rerank_ms`, `top_score` y `no_answer` por mensaje.
- **M13:** golden set separado para validar el umbral; varias URLs válidas por pregunta.
- **M7 — LLM:**
  - Exigir `XAI_API_KEY` al crear `XaiGrokProvider`, con error claro (ADR-006).
  - Verificar que `LLM_MODEL` exista con `GET /v1/models`.
- **M12 — Docker:**
  - Montar `MODEL_CACHE_DIR` como volumen. Embeber ~3500 chunks toma ~2,6 min en CPU: indexar solo si la colección está vacía. Con la caché de embeddings (6 MB) empaquetada, indexar toma ~1,6 s.
  - `QDRANT_URL=http://qdrant:6333` en los servicios; volumen `qdrant_data` (20 MB con 3506 puntos).
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

- **Los tags publicados no se mueven ni se reescriben:** `m00` → `bc14425`, `m01` → `f48a0d3`, `m02` → `4a31943`, `m03` → `731c389`, `m04` → `777402b`, `m05` → `a47323a`, `m06` → merge de M6. Tampoco se reescribe historial ya publicado en `origin`: nada de `push --force` ni rebase de ramas publicadas.
- **Nunca escribir la `XAI_API_KEY`** en código, docs, tests ni commits; solo en `.env`, que está en `.gitignore`.
- **Bancolombia en todo texto visible al usuario** (prompts, UI, respuestas, README, ayuda de la CLI). El código conserva `rag_bbva`. Un test de `tests/unit/test_cli.py` verifica que la ayuda de la CLI diga Bancolombia y no BBVA.
- **Ningún módulo se mergea sin la aprobación explícita** de Juan Felipe; M7 tampoco.
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
7. `docs/modulos/M06.md` (último cerrado; §8 tiene los pendientes) y `M07.md` si existe; luego las bitácoras anteriores si hace falta.
8. `docs/exploracion_sitio.md`: hallazgos del sitio, selectores y riesgos.
9. `CHANGELOG.md`.
