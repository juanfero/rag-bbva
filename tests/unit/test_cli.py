"""Pruebas de la CLI (M0)."""

import pytest
from typer.testing import CliRunner

from rag_bbva import __version__
from rag_bbva.cli import app

runner = CliRunner()


def test_cli_version(clean_env: pytest.MonkeyPatch) -> None:
    """El comando `version` responde con la versión del paquete."""
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"rag-bbva {__version__}"


def test_cli_sin_argumentos_muestra_ayuda(clean_env: pytest.MonkeyPatch) -> None:
    """Sin comando se muestra la ayuda con los comandos disponibles."""
    result = runner.invoke(app, [])

    assert "version" in result.output
