# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]

## [m00] - 2026-10-01 — Fundaciones
### Añadido
- Estructura del paquete `rag_bbva` (layout `src/`) y de `tests/`, `eval/` según la visión general §5.
- `pyproject.toml` con dependencias de M0 y configuración de `ruff` y `pytest` (marcadores `integration`, `slow`).
- `.gitignore` (`.env`, `data/`, modelos, cachés) y `.dockerignore`.
- Configuración externalizada (`config.py`) con todas las variables de §7, validación de rangos y reglas cruzadas; `.env.example` documentado.
- Jerarquía de excepciones con base `RagBbvaError`.
- Logging estructurado en JSON con nivel desde `LOG_LEVEL`.
- CLI con Typer y comando `version`.
- `Dockerfile` base (python:3.11-slim, usuario no root) y `docker-compose.yml` mínimo.
- ADR-006 (clave de xAI opcional en `Settings`) y ADR-007 (dependencias incrementales).
- Bitácora `docs/modulos/M00.md`.

## [Documentación inicial]
### Añadido
- Documentación inicial: visión general, plan de módulos, registro de decisiones y plantilla de bitácora.
### Cambiado
- LLM: Grok (xAI) en lugar de Ollama local (ADR-003); se elimina el servicio `ollama` del compose.
- Se confirman Streamlit como UI y Linux como entorno; M13 entra en alcance.
