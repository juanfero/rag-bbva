# Registro de decisiones (ADR)

Formato: una entrada por decisión. Estado: Propuesta · Aceptada · Reemplazada.

---

## ADR-001 — Orquestación RAG propia, sin LangChain/LlamaIndex
- **Estado:** Aceptada
- **Contexto:** el caso evalúa patrones de diseño y decisiones técnicas defendibles.
- **Decisión:** implementar retriever, reranker, prompts y fachada con código propio.
- **Consecuencias:** + patrones visibles y testeables, + menos dependencias; − más código que escribir.

## ADR-002 — Qdrant como base vectorial
- **Estado:** Aceptada
- **Contexto:** se valoran opciones gratuitas/self-hosted; debe correr en Docker.
- **Decisión:** Qdrant self-hosted; modo `:memory:` del cliente para tests.
- **Consecuencias:** + filtros por payload, imagen oficial ligera; − un servicio más en compose.

## ADR-003 — LLM: Grok (xAI) vía API compatible con OpenAI
- **Estado:** Aceptada (2026-10-01, reemplaza la propuesta de Ollama local)
- **Contexto:** el caso permite APIs de pago (no suman puntos). Un LLM local en CPU sería lento y pesado en RAM.
- **Decisión:** Grok de xAI con el SDK `openai` (`base_url=https://api.x.ai/v1`, `XAI_API_KEY`), detrás de `LLMProvider` (Strategy). Embeddings, reranker y base vectorial siguen siendo open source y locales.
- **Consecuencias:** + mejor calidad en español y menor latencia; + contenedores livianos (sin modelo de varios GB); − costo por token y dependencia externa (mitigado con tope de tokens, contexto acotado y `FakeLLMProvider` en tests); − no suma puntos en "herramientas sin costo", se declara en el README. Volver a un modelo open source = implementar otro `LLMProvider` (mejora futura).

## ADR-004 — SQLite para el historial de conversaciones
- **Estado:** Aceptada
- **Decisión:** SQLite + SQLAlchemy detrás de un Repository.
- **Consecuencias:** + cero infraestructura, persistente en volumen; − no apto para alta concurrencia (declarado como limitación).

## ADR-005 — Embeddings `multilingual-e5-small` y reranker mMiniLM
- **Estado:** Aceptada
- **Decisión:** modelos multilingües livianos que corren en CPU.
- **Consecuencias:** + gratis y rápidos; − menor calidad que `bge-m3` / `bge-reranker-v2-m3` (mejora futura).

## ADR-006 — `XAI_API_KEY` opcional en `Settings`, obligatoria al crear el proveedor
- **Estado:** Aceptada (2026-10-01, M0)
- **Contexto:** la clave es obligatoria para usar Grok, pero la configuración también la cargan los tests, la CLI (`version`, `scrape`, `clean`…) y el proveedor `fake`, que no la necesitan.
- **Decisión:** `xai_api_key: SecretStr | None = None` en `Settings`. La ausencia se valida al construir `XaiGrokProvider` (M7) con un error claro, antes de iniciar una conversación.
- **Consecuencias:** + los comandos que no usan el LLM funcionan sin clave; + la clave nunca aparece en `repr`/logs; − el error por falta de clave no se detecta al cargar la configuración sino al crear el proveedor.

## ADR-007 — Dependencias incrementales por módulo
- **Estado:** Aceptada (2026-10-01, M0)
- **Contexto:** el plan pide no adelantar código de módulos futuros; instalar todo el stack (torch, qdrant-client, fastapi…) desde M0 alarga builds sin uso.
- **Decisión:** `pyproject.toml` solo declara lo que el código ya usa (M0: `pydantic`, `pydantic-settings`, `typer`; dev: `pytest`, `pytest-cov`, `ruff`). Cada módulo agrega sus dependencias en su propio commit.
- **Consecuencias:** + imagen y entorno livianos, historial de dependencias trazable por módulo; − el `pyproject` crece a lo largo del proyecto.

## ADR-008 — Fuente de datos: Bancolombia en lugar de BBVA Colombia
- **Estado:** Aceptada (2026-10-01, M1; reemplaza el supuesto original de S-02)
- **Contexto:** al iniciar M1, `www.bbva.com.co` devolvió **403** (página de bloqueo de WAF con *Reference ID*) a `robots.txt`, la home y `sitemap.xml` para todos los clientes probados: `RAG-BBVA-TechTest/1.0`, `curl` y `urllib` de Python. Sin leer `robots.txt` no se puede verificar qué está permitido (S-08). Eludir el bot-manager con un navegador simulado se descartó por respeto al sitio. El caso permite usar otro banco. Se sondearon 8 bancos colombianos con una petición por sitio: Davivienda (Incapsula), Banco de Bogotá, Banco de Occidente e Itaú bloquean; AV Villas, Banco Popular y Bancolombia responden.
- **Decisión:** usar `https://www.bancolombia.com/`. Su `robots.txt` permite `User-agent: *` salvo rutas puntuales, menciona el uso para RAG de forma explícita y declara un índice de sitemaps por sección. BBVA Colombia se mantiene como **cliente ficticio**; no se renombran el repo, el paquete ni las variables. Cambian `TARGET_BASE_URL` y el default de `QDRANT_COLLECTION` (`bancolombia_docs`).
- **Consecuencias:** + scraping real y respetuoso; + contenido rico y segmentado por sección; − el nombre `rag-bbva` ya no coincide con la fuente (se explica en el README); − Bancolombia bloquea en `robots.txt` los bots de *entrenamiento* de IA (GPTBot, ClaudeBot…). Nuestro uso es recuperación (RAG) con un User-Agent propio, no entrenamiento; se declara en el README.

## ADR-009 — Scraping con httpx sin renderizar JavaScript
- **Estado:** Aceptada (2026-10-01, checkpoint de M1)
- **Contexto:** la visión general dejaba Playwright como plan B si el sitio dependía de JS. En M1 se comparó el HTML estático con el renderizado por Chromium headless en 12 páginas de las 6 secciones de Bancolombia: la cobertura del vocabulario renderizado fue de 0,78 a 1,00. Lo que solo aparece tras renderizar es el banner de cookies, carruseles promocionales y listas de enlaces ("preguntas relacionadas", tarjetas de artículos) hacia páginas que ya están en los sitemaps. El cuerpo de cada página está completo en el HTML estático.
- **Decisión:** el crawler usa `httpx` + BeautifulSoup/lxml (y `trafilatura` en M3), sin navegador headless. Playwright no es dependencia del proyecto: se instaló de forma temporal solo para la medición de M1 (`scripts/explore_site.py --render`).
- **Consecuencias:** + imagen Docker liviana y crawl rápido y barato para el sitio; + el banner de cookies no contamina el texto; − no se capturan los widgets dinámicos (su contenido llega por las páginas enlazadas); − si el sitio migra a una SPA habrá que reevaluar con el mismo script.

## ADR-010 — Sala de prensa fuera del alcance del scraping
- **Estado:** Aceptada (2026-10-02, revisión de M2; acota S-03)
- **Contexto:** S-03 incluía la sala de prensa dentro de `acerca-de` y pedía guardar su fecha de publicación (`published_at`). En M2 se encontró lo siguiente:
  - Los sitemaps listan **74 URLs únicas** bajo `/acerca-de/sala-prensa/`. Hay **73** en `sitemap-sala-de-prensa.xml`, 4 de ellas repetidas en `sitemap-acerca-de.xml`. La restante solo aparece en `sitemap-personas.xml`. Recuento del 2026-10-02, que coincide con las 74 de `data/exploration/urls.txt` de M1.
  - Esas URLs responden **301 hacia `prensa.bancolombia.com`**, un host distinto. En el manifest de M2 (`data/raw/manifest.jsonl` y la copia de la corrida 1), la única URL de sala de prensa procesada termina en la **portada** `https://prensa.bancolombia.com/`, no en la noticia: **1 de 1**. Durante M2 se comprobó a mano el 301 hacia ese host en 4 URLs, sin registrar su destino exacto.
  - Si el sitio redirige a la portada, seguir la redirección no da la noticia. Incluirla exigiría explorar y crawlear un segundo sitio, con su propio `robots.txt`, sitemaps y plantillas.
- **Decisión:** las URLs `/acerca-de/sala-prensa/…` quedan **fuera del alcance**. No se agrega `prensa.bancolombia.com` como host permitido. El crawler no las filtra de antemano: hace una petición, recibe el 301 a otro host, no lo sigue y las registra como `redireccion_omitida`, sin guardar HTML. El resto de `acerca-de` sigue dentro. `published_at` pasa a ser un campo opcional de M3: se extrae solo si la página trae la fecha en metadatos (p. ej. `article:published_time`) y no es obligatorio.
- **Consecuencias:**
  - \+ Se mantiene un solo dominio con una sola política de robots.
  - \+ No se presenta como dato una noticia que no se obtuvo.
  - − El asistente no responde sobre noticias ni comunicados de prensa (limitación L-06 del README).
  - − Un crawl completo gasta unas 74 peticiones en redirecciones omitidas.
  - Mejora futura: crawlear `prensa.bancolombia.com` como host adicional permitido, con su propio `robots.txt` y sitemap.
- **Actualización (2026-10-02, M3):** desde M3 el crawler sí las filtra antes de pedirlas, con `CRAWL_EXCLUDE_PATH_PREFIXES` (por defecto `["/acerca-de/sala-prensa/"]`). Quedan en el manifest como `excluida`, sin petición HTTP y sin consumir cupo de `max_pages`. Eso elimina las ~74 peticiones de la consecuencia anterior.
