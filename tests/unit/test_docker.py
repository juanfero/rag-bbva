"""Estructura del despliegue con Docker (M12): el compose y el Dockerfile cumplen lo que
el README promete. La prueba real del arranque está en M12.md §6 y scripts/smoke_test.sh."""

import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((RAIZ / "docker-compose.yml").read_text("utf-8"))
DOCKERFILE = (RAIZ / "Dockerfile").read_text("utf-8")
SERVICIOS = COMPOSE["services"]


def test_cuatro_servicios_encadenados_por_condicion() -> None:
    assert set(SERVICIOS) == {"qdrant", "init", "api", "ui"}
    assert SERVICIOS["init"]["depends_on"] == {"qdrant": {"condition": "service_healthy"}}
    assert SERVICIOS["api"]["depends_on"] == {
        "init": {"condition": "service_completed_successfully"}
    }
    assert SERVICIOS["ui"]["depends_on"] == {"api": {"condition": "service_healthy"}}
    for nombre in ("qdrant", "api", "ui"):
        assert "healthcheck" in SERVICIOS[nombre], nombre


def test_init_no_scrapea_y_la_api_escucha_en_todas_las_interfaces() -> None:
    assert SERVICIOS["init"]["command"] == ["rag-bbva", "bootstrap"]
    assert "scrape" not in str(SERVICIOS)
    assert SERVICIOS["api"]["command"][-4:] == ["--host", "0.0.0.0", "--port", "8000"]
    assert (
        "--api-url" in SERVICIOS["ui"]["command"]
        and "http://api:8000" in SERVICIOS["ui"]["command"]
    )
    for nombre in ("init", "api"):
        assert SERVICIOS[nombre]["environment"]["QDRANT_URL"] == "http://qdrant:6333"


def test_puertos_solo_locales_y_configurables() -> None:
    assert SERVICIOS["api"]["ports"] == ["127.0.0.1:${API_PUBLISHED_PORT:-8000}:8000"]
    assert SERVICIOS["ui"]["ports"] == ["127.0.0.1:${UI_PUBLISHED_PORT:-8501}:8501"]
    for servicio in SERVICIOS.values():
        assert all(p.startswith("127.0.0.1:") for p in servicio.get("ports", []))


def test_datos_en_volumenes_con_nombre_y_clave_solo_por_env_file() -> None:
    assert set(COMPOSE["volumes"]) == {"qdrant_data", "app_data", "model_cache"}
    for nombre in ("init", "api", "ui"):
        assert set(SERVICIOS[nombre]["volumes"]) == {
            "app_data:/app/data",
            "model_cache:/app/models",
        }
        assert SERVICIOS[nombre]["env_file"] == [{"path": ".env", "required": False}]
    texto = (RAIZ / "docker-compose.yml").read_text("utf-8")
    assert not re.search(r"(GEMINI|XAI)_API_KEY\s*[:=]", texto)


def test_dockerfile_multi_stage_cpu_no_root_con_snapshot() -> None:
    assert len(re.findall(r"^FROM ", DOCKERFILE, re.M)) == 2
    assert "download.pytorch.org/whl/cpu" in DOCKERFILE
    assert "USER app" in DOCKERFILE and "--uid 1000" in DOCKERFILE
    assert "COPY snapshot ./snapshot" in DOCKERFILE
    assert "HF_HOME=/app/models" in DOCKERFILE


def test_pagina_metricas_viaja_en_la_instalacion() -> None:
    pyproject = (RAIZ / "pyproject.toml").read_text("utf-8")
    assert '"rag_bbva.ui" = ["pages/*.py"]' in pyproject


def test_dockerignore_no_envia_secretos_ni_datos() -> None:
    ignorados = (RAIZ / ".dockerignore").read_text("utf-8").split()
    assert {".env", "data", "models", ".git"} <= set(ignorados)
    assert "snapshot" not in ignorados
