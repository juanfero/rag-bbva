"""Interfaz de línea de comandos del proyecto.

Uso: `python -m rag_bbva.cli <comando>` o `rag-bbva <comando>`.
Los comandos de cada etapa (scrape, clean, ingest, chat, metrics) se agregan en
sus módulos respectivos.
"""

import typer

from rag_bbva import __version__
from rag_bbva.logging_conf import configure_logging

app = typer.Typer(
    name="rag-bbva",
    help="Asistente RAG sobre el sitio de BBVA Colombia.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Inicializa el logging antes de ejecutar cualquier comando."""
    configure_logging()


@app.command()
def version() -> None:
    """Muestra la versión instalada del paquete."""
    typer.echo(f"rag-bbva {__version__}")


if __name__ == "__main__":
    app()
