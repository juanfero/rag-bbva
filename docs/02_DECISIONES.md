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
