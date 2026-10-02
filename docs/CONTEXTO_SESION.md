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

Después de `m01`, `main` recibió tres commits `docs` directos, hechos a pedido explícito de Juan Felipe:
- `5edf0dd`: entorno final en M00.md. Es anterior a M1; el tag `m00` no se movió.
- `c38abdc`: README inicial.
- `9a6e3ce`: regla del README incremental. Es la punta actual de `main` y `origin/main`.

### Módulo en curso: M2 — Scraper (datos crudos)
- **Rama activa:** `feat/m02-scraper`, publicada en `origin` y con punta en `55d24de`. **Sin merge ni tag `m02`.**
- **Estado:** implementación, tests (176 en verde), corridas reales y documentación listas. Está **en revisión**, esperando la aprobación de Juan Felipe y su respuesta a la pregunta abierta de §3.
- Hecho en M2, ver `docs/modulos/M02.md`:
  - `BaseCrawler` (Template Method) y `SitemapBfsCrawler`, que leen los dos índices de sitemap.
  - Normalización de URLs, reintentos con backoff solo ante errores transitorios y corte ante ráfagas de 403/429.
  - Validación de dominio y `robots.txt` en cada redirección.
  - `data/raw/pages/<sha1>.html` + `manifest.jsonl` con `lastmod`, y re-ejecución incremental por huella del texto visible.
  - CLI `scrape [--max-pages N]`.
  - Corridas reales con `--max-pages 50`.
  - README, CHANGELOG y bitácora actualizados.
- **Falta para cerrar M2:**
  1. Aprobación de Juan Felipe.
  2. Respuesta sobre la sala de prensa (§3).
  3. Cerrar la bitácora (estado ✅, fecha, lista final de commits).
  4. Pasar la entrada de M2 del CHANGELOG a `## [m02]`.
  5. Marcar M2 ✅ con tag `m02` en la tabla de estado del README.
  6. Merge `--no-ff` a `main`, tag `m02` y push de `main`, la rama y el tag.

### README incremental
- **Parte A** (README inicial en `main`, DoD punto 7, M14 como pulido final y regla en `CLAUDE.md`): **hecha** y publicada en `main` (`c38abdc`, `9a6e3ce`).
- **Parte B** (implementar M2): **hecha** en `feat/m02-scraper`. El README de la rama ya refleja M2 como "🚧 en revisión".

### Estado del árbol (al escribir este archivo)
- Rama `feat/m02-scraper`, sincronizada con `origin`, sin cambios sin commitear y sin stashes.
- Solo en local, ignorado por git: `.venv/` y `data/`.
  - `data/exploration/`: informe de M1.
  - `data/raw/`: 36 HTML, manifest de 50 entradas y `crawl_report.json` de la corrida 3 de M2.
  - Copias de la corrida 1 de M2 (`data/crawl_report_run1.json`, `data/manifest_run1.jsonl`) y logs de las corridas.
  - No hay `.env` en el proyecto; hoy ningún comando lo necesita.

---

## 2. Último pedido de Juan Felipe y hasta dónde se llegó

Pedido de trabajo, tal cual (el siguiente mensaje fue el de este traspaso):

```
M1 revisado: aprobado.

Antes de M2, un cambio de proceso: el README se construye de forma incremental,
no al final, porque el jurado de la sustentación lo revisará en cualquier momento.

PARTE A — README inicial (commit docs directo en main, sin tocar tags; push):
1. Crea README.md con esta estructura:
   - Título + 1 párrafo de qué es el proyecto.
   - Nota visible al inicio: la fuente es Bancolombia porque bbva.com.co responde 403 a
     cualquier crawler (enlace a ADR-008); el código conserva el nombre rag_bbva.
   - "Estado del proyecto": tabla M0–M14 (módulo | descripción | estado ✅/🚧/⏳ | tag).
   - Arquitectura: los diagramas de flujo de ingesta y consulta de 00_VISION_GENERAL §3.
   - Requisitos previos (Docker, Python 3.11, uv, variables de entorno; XAI_API_KEY).
   - Instalación y ejecución: lo que YA funciona (clonar, uv venv, instalar, pytest,
     ruff, CLI version, scripts/explore_site.py). Docker completo: "🚧 Se completa en M12".
   - Uso de la interfaz conversacional: "🚧 Se completa en M10".
   - Patrones de diseño: tabla (patrón | dónde | por qué | estado), marcando cuáles ya
     están implementados (si M1 ya aplica alguno, inclúyelo con ruta de archivo real).
   - Stack tecnológico y justificación (de §4), enlazando los ADR.
   - Decisiones de diseño y supuestos (resumen + enlace a 02_DECISIONES.md y §9).
   - Limitaciones conocidas (L-01, L-02… desde §12; esa tabla pasa a vivir en el README
     y §12 la enlaza).
   - Futuras mejoras.
   - Estructura del repositorio y enlaces a docs/ (plan, bitácoras, exploración).
   Escribe solo hechos verificables. No anuncies como hecho nada que no esté
   implementado.
2. Actualiza 01_PLAN_DE_MODULOS.md:
   - Definition of Done, punto 7: "README actualizado con lo que aporta el módulo
     (estado, uso, patrones, limitaciones)".
   - M14: deja de ser "redactar README" y pasa a "pulido final del README y verificación
     desde cero en una carpeta limpia".
3. Agrega esa misma regla a CLAUDE.md, actualiza CHANGELOG, commit y push.

PARTE B — Implementa SOLO M2 (Scraper, datos crudos) según el plan, en
feat/m02-scraper:
- Template Method en BaseCrawler y un SitemapBfsCrawler que lea LOS DOS índices de
  sitemap. Reutiliza el robots/fetcher de M1 (sin duplicar código).
- Normalización de URLs (utm_*, fragmentos, barras finales), rate limit, backoff solo
  en errores transitorios, abortar ante ráfagas de 403/429, y validar dominio y robots
  en cada redirección.
- data/raw/pages/<sha1>.html + data/raw/manifest.jsonl (incluye lastmod del sitemap),
  re-ejecución incremental por hash.
- CLI: scrape [--max-pages N].
- Tests con respx, sin red, cubriendo todos los casos del plan.
- Corrida real con --max-pages 50 como evidencia en la bitácora: páginas OK/error,
  tiempo total y 2–3 ejemplos del manifest.
- README actualizado (estado M2, comando scrape, patrón Template Method con ruta real).
NO hagas merge: muéstrame pytest, ruff, git log --graph, el resumen de la corrida real y
el diff del README.
```

**Hasta dónde se llegó:** las Partes A y B están completas. Se le mostraron a Juan Felipe pytest (176 passed), ruff, el `git log --graph`, el resumen de las corridas y el diff del README. Quedó pendiente su revisión y la respuesta a la pregunta de §3. **No se hizo merge de M2.**

---

## 3. Decisiones y temas abiertos que NO están en los ADR, en `00_VISION_GENERAL.md §9` ni en `CLAUDE.md`

### Pregunta abierta (sin decidir)
- **Sala de prensa en otro host.** Juan Felipe pidió guardar `published_at` para `acerca-de`/sala de prensa (S-03, se implementa en M2/M3). En M2 se verificó que las URLs `/acerca-de/sala-prensa/…` (74 en los sitemaps) redirigen con 301 a `prensa.bancolombia.com`; se comprobó en 4 de ellas. Con S-03 (otros dominios excluidos) no se descargan: `outcome = redireccion_omitida`. Opciones planteadas:
  - **(a)** Ampliar S-03 a `prensa.bancolombia.com`, leyendo y respetando su propio `robots.txt`.
  - **(b)** Mantener el alcance y declarar que las noticias quedan fuera.

  Está registrada como limitación L-06 en el README y en `M02.md §8`. **No implementar nada hasta que Juan Felipe decida.**

### Decisiones de implementación de M2
Están documentadas en `docs/modulos/M02.md §4` y en la sección "Decisiones" del README, **no** en ADR. Se consideraron dentro del alcance ya aprobado:
- Semillas intercaladas por sección (`interleave_by_section`), para que un crawl parcial cubra las 6 secciones.
- Detección incremental por **huella del texto visible** (`text_fingerprint`), no por bytes. Bancolombia inyecta atributos volátiles en cada respuesta (rpid de Dynatrace, ids aleatorios de `<link>`/`<script>`, recurso de Incapsula).
- Deduplicación por URL final: una semilla ya obtenida vía redirección se omite sin volver a pedirla.
- `BlockGuard` corta solo ante **5 respuestas 403/429 consecutivas**. Hay 403 `AccessDenied` de S3 (páginas muertas del sitemap) intercalados con 200 que no deben abortar.
- Los errores HTTP con cuerpo ≤ 1 KB guardan un fragmento del cuerpo en el campo `error` del manifest.
- `FetchResult.attempts` cuenta todas las peticiones HTTP de una descarga: reintentos y saltos de redirección.

### Prácticas acordadas en la conversación
No están escritas en `CLAUDE.md`; la forma de trabajo de §4 las recoge:
- Se hacen commits directos en `main` **solo** cuando Juan Felipe lo pide explícitamente; hasta ahora, únicamente commits `docs`.
- Las ramas de módulo también se publican en `origin`, no solo `main` y los tags.
- `git commit --amend` solo para commits **no publicados**. El historial publicado no se reescribe; por ejemplo, el alcance mixto del commit `41278e3` quedó anotado en `M01.md §9`.

Todo lo demás decidido en la conversación sí está registrado:
- ADR-001 a 009.
- Supuestos S-01 a S-08 (S-02, S-03 y S-04 confirmados).
- Limitaciones L-01 a L-08 en el README.
- Reglas en `CLAUDE.md`: fuente Bancolombia en textos visibles y README incremental.
- Plan actualizado para M2, M3, M12 y M14.

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
pytest                                   # hoy: 176 passed (sin red)
pytest -m "not integration and not slow"
ruff check . && ruff format --check .
python -m rag_bbva.cli version
python -m rag_bbva.cli scrape --max-pages 50   # red real: ~77 s contra www.bancolombia.com
docker build -t rag-bbva:latest .
```

---

## 6. Pendientes asignados a módulos futuros y deuda técnica

Fuentes: `docs/01_PLAN_DE_MODULOS.md`, las bitácoras §8 y el README.

- **M2 (cierre):** ver §1 "Falta para cerrar M2" y la pregunta de §3.
- **M3 — Limpieza:**
  - `trafilatura` con fallback de selectores `main` → `#main-content` → `[role=main]` (`docs/exploracion_sitio.md §5`).
  - Quitar `header`, `nav`, `footer`, menús de portlet con placeholders `${…}` y cajas `.lrpError`.
  - Eliminar el bloque rotativo de "artículos relacionados" (L-08).
  - Deduplicar soft-404 por hash de texto.
  - Leer solo las entradas del manifest con `path`, descartar `duplicada` y usar `final_url` como URL canónica.
  - Campo `published_at` (opcional) **sujeto a la decisión de §3**.
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

- **Los tags publicados no se mueven ni se reescriben:** `m00` → `bc14425`, `m01` → `f48a0d3`. Tampoco se reescribe historial ya publicado en `origin`: nada de `push --force` ni rebase de ramas publicadas.
- **Nunca escribir la `XAI_API_KEY`** en código, docs, tests ni commits; solo en `.env`, que está en `.gitignore`.
- **Bancolombia en todo texto visible al usuario** (prompts, UI, respuestas, README, ayuda de la CLI). El código conserva `rag_bbva`. Un test de `tests/unit/test_cli.py` verifica que la ayuda de la CLI diga Bancolombia y no BBVA.
- **No hacer merge de `feat/m02-scraper`** sin la aprobación de Juan Felipe.
- **No empezar M3** hasta cerrar M2.
- **Cortesía con el sitio:** respetar `robots.txt`, User-Agent `RAG-BBVA-TechTest/1.0`, pausa ≥ 1 s, sin seguir redirecciones a otros dominios y sin eludir el WAF o el bot-manager.
- `data/` y `.env` no se versionan.

---

## 8. Orden de lectura recomendado

1. `docs/CONTEXTO_SESION.md` (este archivo).
2. `CLAUDE.md`: reglas obligatorias.
3. `README.md`: estado, uso, patrones y limitaciones L-01 a L-08.
4. `docs/00_VISION_GENERAL.md`: requisitos, arquitectura, configuración §7 y supuestos §9.
5. `docs/01_PLAN_DE_MODULOS.md`: Definition of Done y el módulo en curso o siguiente.
6. `docs/02_DECISIONES.md`: ADR-001 a ADR-009.
7. `docs/modulos/M02.md` (en curso); luego `M01.md` y `M00.md` si hace falta.
8. `docs/exploracion_sitio.md`: hallazgos del sitio, selectores y riesgos, necesarios para M3.
9. `CHANGELOG.md`.
