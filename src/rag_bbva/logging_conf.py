"""Configuración de logging estructurado (una línea JSON por evento)."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from rag_bbva.config import get_settings

# Atributos estándar de LogRecord que no se copian como campos extra.
_ATRIBUTOS_ESTANDAR = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None)).keys()
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formatea cada registro como un objeto JSON en una sola línea.

    Los campos pasados con `extra={...}` se incluyen como claves de primer nivel,
    lo que facilita filtrar y agregar logs sin parsear texto libre.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serializa el registro a JSON."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _ATRIBUTOS_ESTANDAR and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | None = None) -> None:
    """Configura el logger raíz con salida JSON a stderr.

    Args:
        level: nivel de logging; si es `None` se usa `LOG_LEVEL` de la configuración.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level or get_settings().log_level)
