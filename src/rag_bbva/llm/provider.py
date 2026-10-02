"""Proveedores de LLM (patrón Strategy).

- `XaiGrokProvider`: Grok de xAI con el SDK `openai` (API compatible con OpenAI).
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
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

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


def _mensaje_amigable(exc: Exception) -> str:
    """Mensaje para el usuario, sin trazas ni detalles internos."""
    if isinstance(exc, openai.RateLimitError):
        return (
            "El servicio de respuestas está recibiendo demasiadas solicitudes. "
            "Intenta de nuevo en unos minutos."
        )
    if isinstance(exc, openai.APITimeoutError):
        return "El servicio de respuestas tardó demasiado en contestar. Intenta de nuevo."
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError) or (
        # xAI responde 400 (no 401) ante una clave inválida: "Incorrect API key provided".
        isinstance(exc, openai.BadRequestError) and "api key" in str(exc).lower()
    ):
        return (
            "La clave XAI_API_KEY no es válida o no tiene permisos para este modelo. "
            "Revise que sea la clave secreta de https://console.x.ai (empieza con 'xai-')."
        )
    if isinstance(exc, openai.NotFoundError):
        return "El modelo configurado (LLM_MODEL) no existe. Ejecute `llm-check`."
    return "El servicio de respuestas no está disponible en este momento. Intenta más tarde."


class XaiGrokProvider(LLMProvider):
    """Grok de xAI vía el SDK `openai`, con reintentos propios y registro de tokens."""

    name = "xai"

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
        http_client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`http_client` permite inyectar un cliente HTTP simulado en los tests."""
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,  # un solo mecanismo de reintentos: el propio (tenacity)
            http_client=http_client,
        )

    def _llamar(self, operacion: str, funcion: Callable[[], T]) -> T:
        reintentador = Retrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_exponential(multiplier=self.backoff_seconds, max=_BACKOFF_MAXIMO),
            retry=retry_if_exception_type(_TRANSITORIOS),
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
            raise LLMError(_mensaje_amigable(exc), detail=f"{operacion}: {exc!r}") from exc

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int | None = None
    ) -> LLMResponse:
        inicio = time.perf_counter()
        respuesta = self._llamar(
            "completar",
            lambda: self.client.chat.completions.create(
                model=self.model,
                messages=list(messages),  # type: ignore[arg-type]
                temperature=self.temperature,
                max_tokens=max_tokens or self.max_tokens,
            ),
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
                model=self.model,
                messages=list(messages),  # type: ignore[arg-type]
                temperature=self.temperature,
                max_tokens=max_tokens or self.max_tokens,
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
                raise LLMError(_mensaje_amigable(exc), detail=f"streaming: {exc!r}") from exc

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
        return sorted(m.id for m in modelos)


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
