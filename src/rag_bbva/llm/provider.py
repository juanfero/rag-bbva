"""Proveedores de LLM (patrón Strategy).

- `OpenAICompatibleProvider`: base para APIs compatibles con OpenAI (SDK `openai`), con
  dos variantes: `GeminiProvider` (por defecto, ADR-012) y `XaiGrokProvider` (ADR-003).
- `FakeLLMProvider`: determinista y sin red, para los tests (nunca gasta créditos).

Reintentos: los internos del SDK se desactivan (`max_retries=0`) y se usa **un solo**
mecanismo propio con `tenacity`, como en el scraper. Solo se reintenta ante 429, 5xx,
timeouts y fallos de conexión; los errores de clave, permisos o modelo inexistente no se
reintentan. Así el número de intentos es explícito, se registra en el log y se prueba.
"""

import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from typing import Any, TypeVar

import openai
from pydantic import BaseModel
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from rag_bbva.exceptions import LLMError

logger = logging.getLogger(__name__)

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}
T = TypeVar("T")

# Errores transitorios: se reintentan con backoff.
_TRANSITORIOS = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APITimeoutError,
    openai.APIConnectionError,
)
_BACKOFF_MAXIMO = 30.0


def is_daily_quota_exhausted(exc: BaseException) -> bool:
    """429 por cupo **diario** agotado (p. ej. el nivel gratuito de Gemini, con quotaId
    `…PerDay…`). Reintentar en segundos no sirve: el cupo se reinicia al día siguiente."""
    if not isinstance(exc, openai.RateLimitError):
        return False
    return "perday" in str(exc).lower().replace("_", "").replace("-", "")


def _es_transitorio(exc: BaseException) -> bool:
    return isinstance(exc, _TRANSITORIOS) and not is_daily_quota_exhausted(exc)


class LLMResponse(BaseModel):
    """Respuesta completa del modelo, con tokens y latencia (para la analítica de M11)."""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    finish_reason: str | None = None


class LLMStream:
    """Respuesta en streaming: se itera por fragmentos de texto; al terminar,
    `response` tiene el texto completo, los tokens y la latencia."""

    def __init__(self, fragmentos: Iterator[str], cierre: Callable[[str], LLMResponse]) -> None:
        self._fragmentos = fragmentos
        self._cierre = cierre
        self._partes: list[str] = []
        self.response: LLMResponse | None = None

    def __iter__(self) -> Iterator[str]:
        for fragmento in self._fragmentos:
            self._partes.append(fragmento)
            yield fragmento
        self.response = self._cierre("".join(self._partes))


class LLMProvider(ABC):
    """Interfaz de los proveedores de LLM."""

    name: str
    model: str

    @abstractmethod
    def complete(
        self, messages: Sequence[Message], *, max_tokens: int | None = None
    ) -> LLMResponse:
        """Respuesta completa a una conversación."""

    @abstractmethod
    def stream(self, messages: Sequence[Message], *, max_tokens: int | None = None) -> LLMStream:
        """Respuesta en streaming."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Ids de los modelos disponibles para la clave configurada."""


class OpenAICompatibleProvider(LLMProvider):
    """Proveedor sobre una API compatible con OpenAI, con el SDK `openai`.

    Reintentos propios, streaming y registro de tokens. Las subclases fijan el nombre del
    proveedor, la variable de su clave y dónde se gestiona, para los mensajes de error.
    """

    name = "openai_compatible"
    key_env = "API_KEY"
    console_url = ""
    key_hint = ""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 800,
        timeout: float = 60,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        reasoning_effort: str | None = None,
        http_client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`reasoning_effort` se envía solo si no es `None` (p. ej. "none" en Gemini 2.5
        para apagar el razonamiento interno). `http_client` permite inyectar un cliente
        HTTP simulado en los tests."""
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.reasoning_effort = reasoning_effort
        self._sleep = sleep
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,  # un solo mecanismo de reintentos: el propio (tenacity)
            http_client=http_client,
        )

    def _mensaje_amigable(self, exc: Exception) -> str:
        """Mensaje para el usuario, sin trazas ni detalles internos."""
        texto = str(exc).lower()
        if is_daily_quota_exhausted(exc):
            return (
                f"Se agotó el cupo diario de {self.key_env} (nivel gratuito). Se reinicia a "
                "medianoche del Pacífico; para seguir antes, cree una clave en un proyecto "
                f"nuevo en {self.console_url} (el cupo es por proyecto, no por clave) y "
                f"reemplace {self.key_env} en .env."
            )
        if isinstance(exc, openai.RateLimitError):
            return (
                "El servicio de respuestas alcanzó su límite de solicitudes o su cupo "
                "(por minuto o por día). Intenta de nuevo en unos minutos."
            )
        if isinstance(exc, openai.APITimeoutError):
            return "El servicio de respuestas tardó demasiado en contestar. Intenta de nuevo."
        if isinstance(exc, openai.PermissionDeniedError) and (
            "credits" in texto or "spending limit" in texto
        ):
            # xAI responde 403 cuando el equipo no tiene créditos o llegó al límite mensual.
            return (
                f"La cuenta de {self.name} no tiene créditos disponibles o alcanzó su límite "
                f"de gasto mensual. Revise la facturación en {self.console_url}."
            )
        if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError) or (
            # xAI y Gemini responden 400 (no 401) ante una clave inválida.
            isinstance(exc, openai.BadRequestError) and "api key" in texto
        ):
            pista = f" ({self.key_hint})" if self.key_hint else ""
            return (
                f"La clave {self.key_env} no es válida o no tiene permisos para este modelo. "
                f"Revise la clave en {self.console_url}{pista}."
            )
        if isinstance(exc, openai.NotFoundError):
            return "El modelo configurado (LLM_MODEL) no existe. Ejecute `llm-check`."
        return "El servicio de respuestas no está disponible en este momento. Intenta más tarde."

    def _llamar(self, operacion: str, funcion: Callable[[], T]) -> T:
        reintentador = Retrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_exponential(multiplier=self.backoff_seconds, max=_BACKOFF_MAXIMO),
            retry=retry_if_exception(_es_transitorio),
            sleep=self._sleep,
            reraise=True,
            before_sleep=lambda estado: logger.warning(
                "Error transitorio del LLM, se reintenta",
                extra={"operacion": operacion, "intento": estado.attempt_number},
            ),
        )
        try:
            return reintentador(funcion)
        except openai.OpenAIError as exc:
            raise LLMError(self._mensaje_amigable(exc), detail=f"{operacion}: {exc!r}") from exc

    def _parametros(self, messages: Sequence[Message], max_tokens: int | None) -> dict[str, Any]:
        parametros: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if self.reasoning_effort:
            parametros["reasoning_effort"] = self.reasoning_effort
        return parametros

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int | None = None
    ) -> LLMResponse:
        inicio = time.perf_counter()
        respuesta = self._llamar(
            "completar",
            lambda: self.client.chat.completions.create(**self._parametros(messages, max_tokens)),
        )
        uso = respuesta.usage
        eleccion = respuesta.choices[0]
        return LLMResponse(
            text=eleccion.message.content or "",
            model=respuesta.model or self.model,
            prompt_tokens=uso.prompt_tokens if uso else 0,
            completion_tokens=uso.completion_tokens if uso else 0,
            latency_ms=round((time.perf_counter() - inicio) * 1000, 1),
            finish_reason=eleccion.finish_reason,
        )

    def stream(self, messages: Sequence[Message], *, max_tokens: int | None = None) -> LLMStream:
        inicio = time.perf_counter()
        # Solo se reintenta abrir el stream; una vez que llegan tokens no se repite.
        flujo = self._llamar(
            "streaming",
            lambda: self.client.chat.completions.create(
                **self._parametros(messages, max_tokens),
                stream=True,
                stream_options={"include_usage": True},
            ),
        )
        uso: dict[str, int] = {}
        fin: dict[str, str | None] = {"motivo": None}

        def fragmentos() -> Iterator[str]:
            try:
                for trozo in flujo:
                    if trozo.usage:
                        uso["prompt"] = trozo.usage.prompt_tokens
                        uso["completion"] = trozo.usage.completion_tokens
                    for eleccion in trozo.choices:
                        if eleccion.finish_reason:
                            fin["motivo"] = eleccion.finish_reason
                        if eleccion.delta.content:
                            yield eleccion.delta.content
            except openai.OpenAIError as exc:
                raise LLMError(self._mensaje_amigable(exc), detail=f"streaming: {exc!r}") from exc

        def cierre(texto: str) -> LLMResponse:
            return LLMResponse(
                text=texto,
                model=self.model,
                prompt_tokens=uso.get("prompt", 0),
                completion_tokens=uso.get("completion", 0),
                latency_ms=round((time.perf_counter() - inicio) * 1000, 1),
                finish_reason=fin["motivo"],
            )

        return LLMStream(fragmentos(), cierre)

    def list_models(self) -> list[str]:
        modelos = self._llamar("listar modelos", lambda: list(self.client.models.list()))
        # Gemini antepone "models/" a los ids; se quita para compararlos con LLM_MODEL.
        return sorted(m.id.removeprefix("models/") for m in modelos)


class GeminiProvider(OpenAICompatibleProvider):
    """Gemini de Google vía su endpoint compatible con OpenAI (ADR-012)."""

    name = "gemini"
    key_env = "GEMINI_API_KEY"
    console_url = "https://aistudio.google.com/api-keys"


class XaiGrokProvider(OpenAICompatibleProvider):
    """Grok de xAI (ADR-003), alternativa a Gemini con `LLM_PROVIDER=xai`."""

    name = "xai"
    key_env = "XAI_API_KEY"
    console_url = "https://console.x.ai"
    key_hint = "empieza con 'xai-'"


class FakeLLMProvider(LLMProvider):
    """LLM falso para tests: responde con un texto fijo o calculado y registra cada
    llamada. Los tokens se estiman por palabras."""

    name = "fake"

    def __init__(
        self,
        responder: str | Callable[[Sequence[Message]], str] = "Respuesta de prueba [1].",
        *,
        model: str = "fake-model",
        models: Sequence[str] = ("fake-model",),
    ) -> None:
        self.model = model
        self._responder = responder
        self._modelos = list(models)
        self.calls: list[list[Message]] = []

    def _texto(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self._responder(messages) if callable(self._responder) else self._responder

    @staticmethod
    def _tokens(texto: str) -> int:
        return len(texto.split())

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int | None = None
    ) -> LLMResponse:
        texto = self._texto(messages)
        return LLMResponse(
            text=texto,
            model=self.model,
            prompt_tokens=sum(self._tokens(m["content"]) for m in messages),
            completion_tokens=self._tokens(texto),
            latency_ms=0.0,
            finish_reason="stop",
        )

    def stream(self, messages: Sequence[Message], *, max_tokens: int | None = None) -> LLMStream:
        respuesta = self.complete(messages, max_tokens=max_tokens)
        # Fragmentos palabra a palabra conservando los espacios: unidos dan el texto.
        return LLMStream(iter(re.findall(r"\S+\s*", respuesta.text)), lambda _texto: respuesta)

    def list_models(self) -> list[str]:
        return list(self._modelos)
