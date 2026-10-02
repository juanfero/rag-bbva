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
| M2 — Scraper (datos crudos) | `m02` | merge `--no-ff` de `feat/m02-scraper` en `main` (2026-10-02); el hash se ve con `git rev-parse --short m02^{commit}` | `docs/modulos/M02.md` |

- Commits `docs` directos en `main`, pedidos de forma explícita por Juan Felipe:
  - `5edf0dd`, entre `m00` y `m01`.
  - `c38abdc` (README inicial) y `9a6e3ce` (regla del README incremental), entre `m01` y `m02`.
- El merge de M2 incluye además los dos commits de traspaso de sesión y las correcciones de la revisión de M2. Ver `M02.md §10`.

### Siguiente módulo: M3 — Limpieza (datos limpios)
- **No iniciado.** Se empieza solo cuando Juan Felipe lo pida.
- Al empezar: crear la rama `feat/m03-limpieza` desde `main` y copiar `docs/modulos/_PLANTILLA.md` a `docs/modulos/M03.md`.
- Insumos, en `docs/01_PLAN_DE_MODULOS.md` (M3), `docs/exploracion_sitio.md §5` y `M02.md §8`:
  - Leer `data/raw/manifest.jsonl`: solo entradas con `path`, descartar `duplicada` y usar `final_url` como URL canónica.
  - Extraer el contenido principal con `trafilatura` y el fallback `main` → `#main-content` → `[role=main]`.
  - Quitar `header`, `nav`, `footer`, los menús de portlet con `${…}`, las cajas `.lrpError` y el bloque rotativo de "artículos relacionados" (L-08).
  - Deduplicar soft-404 por hash de texto.
  - `published_at`: **opcional**, solo si la página trae la fecha en metadatos (p. ej. `article:published_time`). La sala de prensa está fuera del alcance (ADR-010).

### Estado del árbol (al escribir este archivo)
- Rama `main` con el merge de M2 y el tag `m02`, publicados en `origin`.
- Las ramas `feat/m00-fundaciones`, `feat/m01-exploracion` y `feat/m02-scraper` siguen a sus pares en `origin`.
- Solo en local, ignorado por git: `.venv/` y `data/`.
  - `data/exploration/`: informe de M1.
  - `data/raw/`: 36 HTML, manifest de 50 entradas y `crawl_report.json` de la corrida 3 de M2.
  - Copias de la corrida 1 (`data/crawl_report_run1.json`, `data/manifest_run1.jsonl`) y logs de las corridas 1–3 (`data/scrape_m02*.log`).
  - No hay `.env` en el proyecto; hoy ningún comando lo necesita.

---

## 2. Último pedido de Juan Felipe y hasta dónde se llegó

Revisión de M2: **aprobado con correcciones menores**. Para la sala de prensa eligió la **opción (b)**. Se hizo todo lo pedido:
- ADR-010; S-03, L-06 y la mejora futura actualizados; `published_at` opcional en el plan de M3.
- Conteo en el manifest: **1 de 1** URL de sala de prensa procesada termina en la portada `https://prensa.bancolombia.com/`.
- Fix de `elapsed_ms`, con tests.
- Descripción exacta de los reintentos en el README.
- 74 frente a 73 resuelto: 73 en `sitemap-sala-de-prensa.xml` más 1 que solo está en `sitemap-personas.xml`.
- Corrida 2 y `depth 0` documentadas en `M02.md §6`.
- Upstream de `feat/m00-fundaciones` configurado.
- Cierre de M2: bitácora ✅, CHANGELOG `[m02]`, README ✅, merge `--no-ff`, tag `m02` y push.

Juan Felipe pidió expresamente **no empezar M3**. La siguiente acción es esperar su indicación.

---

## 3. Decisiones y temas abiertos que NO están en los ADR, en `00_VISION_GENERAL.md §9` ni en `CLAUDE.md`

### Preguntas abiertas
- Ninguna. La de la sala de prensa se resolvió con ADR-010.
- Posible mejora, **no pedida ni implementada**: filtrar `/acerca-de/sala-prensa/` antes de pedirla. Ahorraría unas 74 peticiones en un crawl completo, que hoy terminan como `redireccion_omitida`. Consultar con Juan Felipe antes de hacerlo.

### Decisiones de implementación de M2
Están documentadas en `docs/modulos/M02.md §4` y en la sección "Decisiones" del README, **no** en ADR:
- Semillas intercaladas por sección (`interleave_by_section`).
- Detección incremental por **huella del texto visible** (`text_fingerprint`).
- Deduplicación por URL final.
- `BlockGuard` corta solo ante **5 respuestas 403/429 consecutivas**.
- Los errores HTTP con cuerpo ≤ 1 KB guardan un fragmento en `error`.
- `FetchResult.attempts` cuenta todas las peticiones HTTP de una descarga.
- `FetchResult.elapsed_ms` mide desde el inicio de `fetch()`, con pausas y backoff incluidos, en todas las salidas que hicieron al menos una petición.
- El BFS es FIFO: con `--max-pages` bajo solo se procesan semillas (`depth 0`). La profundidad 1 está probada solo con respx.

### Prácticas acordadas en la conversación
No están escritas en `CLAUDE.md`; la forma de trabajo de §4 las recoge:
- Se hacen commits directos en `main` **solo** cuando Juan Felipe lo pide explícitamente; hasta ahora, únicamente commits `docs`.
- Las ramas de módulo también se publican en `origin`, no solo `main` y los tags.
- `git commit --amend` solo para commits **no publicados**. El historial publicado no se reescribe.
- Al pedir evidencia de corridas reales, se afirma solo lo que se puede verificar con archivos. Si un reporte se sobrescribió, se dice. Ejemplo: corrida 2 de M2.

Todo lo demás está registrado:
- ADR-001 a 010.
- Supuestos S-01 a S-08 (S-02, S-03 y S-04 confirmados; S-03 acotado por ADR-010).
- Limitaciones L-01 a L-08 en el README.
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
pytest                                   # hoy: 178 passed (sin red)
pytest -m "not integration and not slow"
ruff check . && ruff format --check .
python -m rag_bbva.cli version
python -m rag_bbva.cli scrape --max-pages 50   # red real: ~77 s contra www.bancolombia.com
docker build -t rag-bbva:latest .
```

---

## 6. Pendientes asignados a módulos futuros y deuda técnica

Fuentes: `docs/01_PLAN_DE_MODULOS.md`, las bitácoras §8 y el README.

- **M3 — Limpieza:**
  - `trafilatura` con fallback de selectores `main` → `#main-content` → `[role=main]` (`docs/exploracion_sitio.md §5`).
  - Quitar `header`, `nav`, `footer`, menús de portlet con placeholders `${…}` y cajas `.lrpError`.
  - Eliminar el bloque rotativo de "artículos relacionados" (L-08).
  - Deduplicar soft-404 por hash de texto.
  - Leer solo las entradas del manifest con `path`, descartar `duplicada` y usar `final_url` como URL canónica.
  - Campo `published_at` opcional, solo desde metadatos (ADR-010).
- **M7 — LLM:**
  - Exigir `XAI_API_KEY` al crear `XaiGrokProvider`, con error claro (ADR-006).
  - Verificar que `LLM_MODEL` exista con `GET /v1/models`.
- **M12 — Docker:**
  - `docker compose up` **no scrapea**: se versiona un snapshot de `data/clean/`, `init` solo indexa si la colección está vacía y el scraping completo es un comando opcional.
  - Volúmenes de `data/` con permisos para el usuario no root (uid 1000). En M0 se quitó el volumen `./data` porque Docker lo creaba como root.
- **M14:**
  - Pulido final del README, manteniendo la nota inicial sobre la fuente Bancolombia con el enlace a ADR-008.
  - Verificación desde cero en una carpeta limpia, siguiendo el README literalmente.
  - Revisión del historial y tag `v1.0.0`.
- **Riesgo operativo:** el sitio carga un recurso de **Incapsula** (bot-manager). A 1 req/s no ha bloqueado, pero el crawl completo (~1.100 páginas, ~27 min) podría activarlo. En ese caso `BlockGuard` corta y conserva lo avanzado.

---

## 7. Lo que la próxima sesión NO debe romper

- **Los tags publicados no se mueven ni se reescriben:** `m00` → `bc14425`, `m01` → `f48a0d3`, `m02` → merge de M2. Tampoco se reescribe historial ya publicado en `origin`: nada de `push --force` ni rebase de ramas publicadas.
- **Nunca escribir la `XAI_API_KEY`** en código, docs, tests ni commits; solo en `.env`, que está en `.gitignore`.
- **Bancolombia en todo texto visible al usuario** (prompts, UI, respuestas, README, ayuda de la CLI). El código conserva `rag_bbva`. Un test de `tests/unit/test_cli.py` verifica que la ayuda de la CLI diga Bancolombia y no BBVA.
- **No empezar M3** hasta que Juan Felipe lo pida. Ningún módulo se mergea sin su aprobación explícita.
- **Cortesía con el sitio:** respetar `robots.txt`, User-Agent `RAG-BBVA-TechTest/1.0`, pausa ≥ 1 s, sin seguir redirecciones a otros dominios y sin eludir el WAF o el bot-manager.
- `data/` y `.env` no se versionan.

---

## 8. Orden de lectura recomendado

1. `docs/CONTEXTO_SESION.md` (este archivo).
2. `CLAUDE.md`: reglas obligatorias.
3. `README.md`: estado, uso, patrones y limitaciones L-01 a L-08.
4. `docs/00_VISION_GENERAL.md`: requisitos, arquitectura, configuración §7 y supuestos §9.
5. `docs/01_PLAN_DE_MODULOS.md`: Definition of Done y el módulo en curso o siguiente.
6. `docs/02_DECISIONES.md`: ADR-001 a ADR-010.
7. `docs/modulos/M02.md` (último cerrado; §8 tiene los pendientes para M3); luego `M01.md` y `M00.md` si hace falta.
8. `docs/exploracion_sitio.md`: hallazgos del sitio, selectores y riesgos, necesarios para M3.
9. `CHANGELOG.md`.
