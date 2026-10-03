# CLAUDE.md — Reglas del proyecto RAG BBVA

> **Si retomas el proyecto en una sesión nueva, lee primero `docs/CONTEXTO_SESION.md`.**

## Contexto
Prueba técnica de ML/AI Engineer: sistema RAG en Python. Cliente ficticio: BBVA Colombia; fuente de datos: https://www.bancolombia.com/ (ADR-008, bbva.com.co bloquea crawlers).
Leer siempre antes de trabajar: `docs/00_VISION_GENERAL.md` y `docs/01_PLAN_DE_MODULOS.md`.

## Forma de trabajo (obligatoria)
1. Se trabaja **un módulo a la vez** (M0 → M14). No adelantar código de módulos futuros.
2. Al iniciar un módulo: crear rama `feat/mXX-nombre` y copiar `docs/modulos/_PLANTILLA.md` a `docs/modulos/MXX.md`.
3. Un módulo se cierra **solo** si cumple la Definition of Done de `01_PLAN_DE_MODULOS.md`:
   `pytest` en verde (incluye módulos anteriores), `ruff check .` limpio, bitácora, CHANGELOG y **README** actualizados.
4. **README incremental:** cada módulo actualiza el README con lo que aporta (estado, uso, patrones, limitaciones). Solo hechos verificables: nada se anuncia como hecho si no está implementado.
5. Si una prueba falla: corregir el código, nunca debilitar ni borrar la prueba para que pase.
6. Ante cualquier ambigüedad del caso: **preguntar a Juan Felipe** antes de implementar; si se decide un supuesto, registrarlo en `00_VISION_GENERAL.md §9` y en `02_DECISIONES.md`.
7. No cambiar el stack de `00_VISION_GENERAL.md §4` sin registrar la decisión (ADR).

## Fuente de datos
- La fuente de datos es **Bancolombia** (ADR-008). El código conserva el nombre `rag_bbva`, pero **todo texto visible al usuario (prompts, UI, respuestas, README) dice Bancolombia**.

## Entorno
- Linux + Docker. Scripts en bash; rutas POSIX.
- LLM: Gemini 2.5 Flash (ADR-012) vía SDK `openai` con `GEMINI_BASE_URL`; Grok (xAI) como alternativa. **Nunca** escribir `GEMINI_API_KEY` ni `XAI_API_KEY` en código, docs, tests ni commits; solo en `.env` (ignorado por git).
- Los tests unitarios usan `FakeLLMProvider` o un transporte simulado: nunca consumen cupo ni créditos del LLM.
- `tests/unit/test_secrets.py` falla si algún archivo del repo contiene una clave de API. Si se agota el cupo gratuito de Gemini, Juan Felipe entrega una clave de un **proyecto nuevo** (el cupo es por proyecto): se pone solo en `.env` y se verifica con `llm-check`.

## Convenciones de código
- Python 3.11, type hints en todo, docstrings en español.
- Toda configuración sale de `rag_bbva.config.get_settings()`; nada hardcodeado.
- Errores: lanzar excepciones de `rag_bbva.exceptions`; nunca `except Exception: pass`.
- Logging con `logging.getLogger(__name__)`, nunca `print` en el core.
- Dependencias externas (red, Qdrant, API del LLM, modelos) detrás de interfaces para poder usar dobles en tests.
- Tests unitarios **sin red**. Tests que requieren servicios reales: `@pytest.mark.integration`; modelos pesados: `@pytest.mark.slow`.

## Git
- Conventional Commits en español: `feat(scraping): …`, `fix(api): …`, `test(memory): …`, `docs: …`, `build: …`, `chore: …`.
- Commits pequeños y lógicos (nunca un solo commit por módulo).
- Al cerrar módulo: merge a `main`, tag `mXX`.

## Comandos
- Tests: `pytest -m "not integration and not slow"` (rápidos) · `pytest` (todos)
- Lint: `ruff check . && ruff format --check .`
- Docker: `docker compose up -d --build`
