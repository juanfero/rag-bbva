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
