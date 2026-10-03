"""Jerarquía de excepciones del proyecto.

Todas heredan de `RagBbvaError` para que las capas superiores (API, CLI, UI) puedan
capturar los errores del dominio en un único punto y responder de forma consistente.
"""


class RagBbvaError(Exception):
    """Error base de todo el sistema RAG BBVA."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        """Crea el error con un mensaje para el usuario y un detalle técnico opcional."""
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        """Devuelve el mensaje, añadiendo el detalle técnico si existe."""
        return f"{self.message} ({self.detail})" if self.detail else self.message


class ConfigurationError(RagBbvaError):
    """Configuración ausente o incoherente detectada en tiempo de ejecución."""


class ScrapingError(RagBbvaError):
    """Fallo al descubrir o descargar páginas del sitio."""


class ProcessingError(RagBbvaError):
    """Fallo al limpiar o normalizar documentos crudos."""


class IndexingError(RagBbvaError):
    """Fallo al generar chunks, embeddings o escribir en la base vectorial."""


class RetrievalError(RagBbvaError):
    """Fallo al recuperar o reordenar chunks relevantes."""


class LLMError(RagBbvaError):
    """Fallo al invocar el modelo de lenguaje (timeout, rate limit, API caída)."""


class HistoryError(RagBbvaError):
    """Fallo al leer o persistir el historial de conversaciones."""


class ConversationNotFoundError(HistoryError):
    """El `conversation_id` pedido no existe en el historial."""


class MessageNotFoundError(HistoryError):
    """El `message_id` pedido no existe en el historial."""
