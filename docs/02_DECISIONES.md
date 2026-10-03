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
- **Estado:** **Reemplazada** por [ADR-012](#adr-012--llm-gemini-25-flash-clave-gratuita-de-ai-studio-grok-queda-como-alternativa) (2026-10-02, M7): el proveedor por defecto es Gemini 2.5 Flash con `GEMINI_API_KEY`. Grok queda solo como alternativa opcional con `LLM_PROVIDER=xai`. Original: aceptada el 2026-10-01, reemplazaba la propuesta de Ollama local
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

## ADR-011 — Alcance: secciones principales más rutas del dominio alcanzadas
- **Estado:** Aceptada (2026-10-02, revisión de M3; amplía S-03)
- **Contexto:** S-03 nombraba 6 secciones, pero el crawl completo de M3 llegó por enlace o redirección a páginas del mismo dominio en otras rutas. La limpieza conservó 7 documentos de ese tipo: `pagos` 4, `puntos-de-atencion` 1, `tramites-digitales` 1 y `tu360` 1. Son contenido público e informativo del mismo sitio.
- **Decisión:** el alcance son las 6 secciones principales **y cualquier ruta del dominio alcanzada por enlace o redirección**. La `section` de cada documento se toma de su URL real (`final_url`). Siguen excluidos los otros hosts, la sala de prensa (ADR-010), los PDFs, los formularios y las rutas prohibidas por robots.
- **Consecuencias:**
  - \+ No se descarta contenido válido del sitio por su ruta.
  - \+ `section` refleja dónde vive la página.
  - − Aparecen secciones con pocos documentos. Los filtros por sección (M5) deben aceptar valores fuera de las 6.

## ADR-012 — LLM: Gemini 2.5 Flash (clave gratuita de AI Studio); Grok queda como alternativa
- **Estado:** Aceptada (2026-10-02, M7; reemplaza ADR-003 como proveedor por defecto)
- **Contexto:**
  - En M7 la API de xAI respondió `403 permission-denied` para la clave del proyecto: *"…has either used all available credits or reached its monthly spending limit"*. Sin créditos no hay respuestas.
  - Juan Felipe decidió cambiar a Gemini con una clave gratuita de Google AI Studio.
  - Gemini ofrece un endpoint compatible con OpenAI (`https://generativelanguage.googleapis.com/v1beta/openai/`), así que se reutilizan el SDK `openai`, los reintentos, el streaming y el registro de tokens de M7.
- **Decisión:**
  - `LLM_PROVIDER=gemini` con `LLM_MODEL=gemini-2.5-flash` y `LLM_REASONING_EFFORT=none`, que apaga el razonamiento interno para bajar latencia y tokens.
  - Grok sigue disponible con `LLM_PROVIDER=xai` (Strategy: `GeminiProvider` y `XaiGrokProvider` comparten `OpenAICompatibleProvider`).
  - Se eligió `gemini-2.5-flash` frente a `gemini-3.8-flash`, el modelo de los ejemplos de AI Studio: el 2.5 respondió en ~0,8 s, mientras que el 3.8 devolvió `503 high demand` en la prueba del 2026-10-02.
  - Precios pagos para estimar el costo equivalente: $0,30 de entrada y $2,50 de salida por millón de tokens (https://ai.google.dev/gemini-api/docs/pricing, actualizado el 2026-10-01).
- **Consecuencias:**
  - \+ Costo real 0 con la clave gratuita: suma en "herramientas sin costo" (R7).
  - \+ Cambiar de proveedor es solo configuración.
  - − **En el nivel gratuito Google puede usar prompts y respuestas para mejorar sus productos, y pueden revisarlos personas** (términos de la Gemini API: *"Do not submit sensitive, confidential, or personal information to the Unpaid Services"*). El contexto es contenido público de Bancolombia, pero las preguntas de los usuarios internos llegan a Google: se declara como limitación en el README. Para uso real habría que pasar al nivel pago, que no usa los datos para mejorar productos.
  - − Los cupos del nivel gratuito son por proyecto y el diario se reinicia a medianoche del Pacífico; las cifras se ven en el panel de AI Studio. Al agotarse, la API responde 429 y el asistente lo informa con un mensaje claro.

## ADR-013 — Historial: un `conversation_id` desconocido se informa, no se crea
- **Estado:** Aceptada (2026-10-03, M8; validada por Juan Felipe en la revisión del módulo)
- **Contexto:** el plan de M8 deja abierto qué hacer con un `conversation_id` inexistente: crearlo al vuelo o informarlo, "según contrato documentado". S-05 decía que el ID lo genera la UI o lo envía el cliente.
- **Decisión:**
  - Las conversaciones se crean de forma explícita (`create_conversation`) y el **servidor** genera el ID (UUID4). El cliente lo recibe y lo reenvía en cada pregunta.
  - Leer o escribir en un ID que no existe lanza `ConversationNotFoundError` (subclase de `HistoryError`); en M9 la API lo traduce a **404**. `POST /chat` sin `conversation_id` creará una conversación nueva.
  - Los mensajes se ordenan por su `id` autoincremental (orden de inserción), no por la hora: dos mensajes en el mismo instante no se desordenan.
  - El título de la conversación es su primera pregunta (una línea, máx. 80 caracteres).
- **Consecuencias:** + un ID mal copiado no abre en silencio una conversación vacía, sin contexto, que el usuario creería continuar; + IDs no adivinables y sin choques entre clientes; − el cliente debe guardar el ID que recibe para continuar la conversación.

## ADR-014 — Turno atómico: la pregunta y la respuesta se guardan juntas o ninguna
- **Estado:** Aceptada (2026-10-03, M9; pedida por Juan Felipe al aprobar M8)
- **Contexto:** si la pregunta se guardara al recibirla y luego fallara el LLM (cupo agotado, timeout) o Qdrant, quedaría una pregunta sin respuesta en el historial. La siguiente pregunta la recibiría como contexto y el reformulador mezclaría un tema que el usuario nunca vio respondido. Si la conversación es nueva, además quedaría una conversación vacía.
- **Decisión:**
  - `RAGService.ask` no escribe nada hasta tener la respuesta. Después, `ConversationRepository.add_turn` guarda la pregunta, la respuesta (con fuentes y métricas) y, si no había `conversation_id`, la conversación nueva, **en una sola transacción** de SQLite.
  - Si falla la recuperación, el LLM o la propia base, no queda nada guardado y la API responde 503 (o 404 si el ID no existía, antes de gastar tokens).
  - Las respuestas `no_answer` (el umbral cortó) sí se guardan: son un turno completo y la analítica de M11 las cuenta.
- **Consecuencias:** + el contexto de la conversación solo tiene turnos completos; + no hay conversaciones vacías por errores; − una pregunta que falló no queda registrada en el historial (sí en el log de la API, como advertencia).

## ADR-015 — En las preguntas de seguimiento, el prompt de respuesta lleva también la pregunta autónoma
- **Estado:** Aceptada (2026-10-03, M9; cambia una decisión de M7)
- **Contexto:** en M7 la pregunta reformulada solo se usaba para recuperar y el LLM respondía con la pregunta original. Con historial real (M9), el LLM recibiría solo "¿y cuáles son los requisitos?" y el contexto recuperado, sin saber de qué producto se habla.
- **Decisión:** si el reformulador usó el LLM, el mensaje del usuario lleva la pregunta original y debajo `Pregunta autónoma (la misma pregunta, reescrita con el historial de la conversación): …`. El prompt de sistema no cambia; `PROMPT_VERSION` pasa a `2026-10-03.1`. Sin reformulación, el prompt queda idéntico al de M7.
- **Consecuencias:** + el modelo responde la pregunta que el usuario quiso hacer, con sus palabras y con el referente resuelto; − si el reformulador interpreta mal la pregunta, la respuesta hereda el error (la pregunta original sigue en el prompt para mitigarlo).
