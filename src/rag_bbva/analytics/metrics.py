"""Analítica del historial de conversaciones (M11).

Lee el historial **solo a través del Repository** (`ConversationRepository.all_messages`),
lo pasa a un `DataFrame` de pandas con una fila por **turno** (pregunta del usuario +
respuesta del asistente) y calcula las métricas. Las definiciones exactas están en
`docs/modulos/M11.md` §3 y en el README; aquí, en el docstring de cada sección.

Convenciones:
- **Turno:** cada respuesta del asistente; su pregunta es el mensaje del usuario anterior
  en la misma conversación. El filtro `since` se aplica a la fecha de los mensajes.
- **Percentiles:** método del rango más cercano (`sorted[ceil(p/100 · n) - 1]`), sin
  interpolar, para que se puedan calcular a mano.
- **Porcentajes:** sobre el total indicado en cada métrica; con total 0, el porcentaje es 0.
- Los textos de preguntas que se muestran pasan por `mask_numbers` (privacidad).
"""

import logging
import math
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import BaseModel

from rag_bbva.analytics.privacy import mask_numbers
from rag_bbva.config import Settings
from rag_bbva.indexing.embedding import Embedder
from rag_bbva.memory.models import Message

logger = logging.getLogger(__name__)

ETAPAS = ("total", "rewrite", "retrieval", "rerank", "llm")
SUPUESTO_IMPACTO = (
    "Estimación: cada consulta resuelta ahorra MANUAL_SEARCH_MINUTES minutos de búsqueda "
    "manual en el sitio. Es un supuesto configurable, no una medición."
)
NOTA_COSTO = (
    "Estimación con los precios pagos de la configuración (LLM_PRICE_*). Con la clave "
    "gratuita de Gemini el costo real es 0."
)


# --- Modelos del resumen ----------------------------------------------------------------


class Count(BaseModel):
    count: int
    pct: float


class Percentiles(BaseModel):
    n: int
    p50: float | None
    p95: float | None


class ConversationStats(BaseModel):
    mean: float
    median: float


class Operational(BaseModel):
    conversations: int
    messages: int
    turns: int
    turns_per_conversation: ConversationStats
    turns_by_day: dict[str, int]
    turns_by_hour: dict[str, int]
    latency_ms: dict[str, Percentiles]


class Feedback(BaseModel):
    up: int
    down: int
    voted: int
    coverage_pct: float
    up_rate_pct: float


class Quality(BaseModel):
    no_answer: Count
    hard_cut: Count
    llm_abstention: Count
    llm_abstention_gray_zone: int
    llm_abstention_outside_gray_zone: int
    gray_zone_turns: Count
    with_sources: Count
    reranker_top_score_mean: float | None
    feedback: Feedback


class RankedItem(BaseModel):
    name: str
    count: int


class QuestionGroup(BaseModel):
    question: str  # representante: la pregunta más repetida del grupo (enmascarada)
    count: int
    examples: list[str]


class Content(BaseModel):
    top_urls: list[RankedItem]
    top_sections: list[RankedItem]
    frequent_questions: list[QuestionGroup]
    content_gaps: list[QuestionGroup]
    grouping: str


class Memory(BaseModel):
    multi_turn_conversations: Count
    rewritten_turns: Count
    turns_with_rewrite_data: int


class Cost(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tokens_per_turn: float
    estimated_cost_usd: float
    estimated_cost_per_turn_usd: float
    price_input_per_mtok: float
    price_output_per_mtok: float
    note: str


class Impact(BaseModel):
    resolved_turns: int
    resolution_rate_pct: float
    manual_search_minutes: float
    hours_saved: float
    cost_per_resolved_usd: float | None
    assumption: str


class Meta(BaseModel):
    generated_at: datetime
    since: datetime | None
    source: str
    demo: bool
    timezone: str
    rerank_hard_min_score: float
    rerank_min_score: float
    faq_similarity: float


class AnalyticsSummary(BaseModel):
    meta: Meta
    operational: Operational
    quality: Quality
    content: Content
    memory: Memory
    cost: Cost
    impact: Impact


# --- Utilidades ---------------------------------------------------------------------------


def percentile(valores: Sequence[float], p: float) -> float | None:
    """Percentil `p` por rango más cercano: `sorted[ceil(p/100 · n) - 1]`."""
    datos = sorted(v for v in valores if v is not None and not math.isnan(v))
    if not datos:
        return None
    indice = max(0, math.ceil(p / 100 * len(datos)) - 1)
    return float(datos[indice])


def _pct(parte: int, total: int) -> float:
    return round(100 * parte / total, 1) if total else 0.0


def _count(parte: int, total: int) -> Count:
    return Count(count=int(parte), pct=_pct(int(parte), total))


def section_of(url: str) -> str:
    """Sección del sitio: primer segmento de la ruta de la URL (`inicio` si no hay)."""
    partes = [p for p in urlsplit(url).path.split("/") if p]
    return partes[0] if partes else "inicio"


def is_demo_source(nombre: str) -> bool:
    """La base es de demostración si su nombre contiene `demo` (p. ej. `demo.db`)."""
    return "demo" in nombre.lower()


# --- Tabla de turnos -------------------------------------------------------------------------

COLUMNAS_TURNOS = [
    "conversation_id",
    "message_id",
    "created_at",
    "question",
    "standalone_question",
    "answer_chars",
    "no_answer",
    "no_answer_type",
    "gray_zone",
    "top_score",
    "n_sources",
    "feedback",
    "total_ms",
    "rewrite_ms",
    "retrieval_ms",
    "rerank_ms",
    "llm_ms",
    "prompt_tokens",
    "completion_tokens",
    "model",
    "rewritten",
]


def turns_frame(messages: Sequence[Message], settings: Settings) -> pd.DataFrame:
    """Una fila por turno. Las preguntas ya van enmascaradas (`mask_numbers`)."""
    zona = ZoneInfo(settings.analytics_timezone)
    filas: list[dict[str, Any]] = []
    ultima_pregunta: dict[str, str] = {}
    for m in messages:
        if m.role == "user":
            ultima_pregunta[m.conversation_id] = m.content
            continue
        x = m.metrics
        pregunta = mask_numbers(ultima_pregunta.pop(m.conversation_id, ""))
        autonoma = mask_numbers(x.rewritten_query) if x.rewritten_query else pregunta
        sin_respuesta = bool(x.no_answer)
        corte_duro = sin_respuesta and (
            x.top_score is None or x.top_score < settings.rerank_hard_min_score
        )
        tipo = "corte_duro" if corte_duro else ("abstencion_llm" if sin_respuesta else "")
        zona_gris = x.gray_zone
        if zona_gris is None and x.top_score is not None:  # mensajes anteriores a M11
            zona_gris = settings.rerank_hard_min_score <= x.top_score < settings.rerank_min_score
        filas.append(
            {
                "conversation_id": m.conversation_id,
                "message_id": m.id,
                "created_at": m.created_at.astimezone(zona),
                "question": pregunta,
                "standalone_question": autonoma,
                "answer_chars": len(m.content),
                "no_answer": sin_respuesta,
                "no_answer_type": tipo,
                "gray_zone": bool(zona_gris),
                "top_score": x.top_score,
                "n_sources": len(m.sources),
                "feedback": m.feedback or "",
                "total_ms": x.total_ms,
                "rewrite_ms": x.rewrite_ms if x.rewritten_query else None,
                "retrieval_ms": x.retrieval_ms,
                "rerank_ms": x.rerank_ms,
                "llm_ms": x.llm_ms if x.llm_ms else None,  # 0 = no se llamó al LLM
                "prompt_tokens": x.prompt_tokens or 0,
                "completion_tokens": x.completion_tokens or 0,
                "model": x.model or "",
                # None si el turno es anterior a M11 (no se guardaba la reformulación).
                "rewritten": None if x.rewrite_ms is None else bool(x.rewritten_query),
            }
        )
    return pd.DataFrame(filas, columns=COLUMNAS_TURNOS)


# --- Agrupación de preguntas -----------------------------------------------------------------


def group_questions(
    preguntas: Sequence[str], embedder: Embedder | None, umbral: float, top_n: int
) -> list[QuestionGroup]:
    """Agrupa preguntas casi iguales.

    Con embedder: greedy y determinista, en orden de aparición. Cada pregunta va al grupo
    cuyo centroide (normalizado) tenga coseno ≥ `umbral` más alto; si ninguno llega, abre
    un grupo nuevo. Sin embedder: por texto normalizado (minúsculas, sin signos).
    El representante es la pregunta más repetida del grupo (la primera, si empatan).
    """
    textos = [p for p in preguntas if p.strip()]
    if not textos:
        return []
    grupos: list[list[str]] = []
    if embedder is None:
        claves: dict[str, int] = {}
        for t in textos:
            clave = " ".join("".join(c for c in t.lower() if c.isalnum() or c.isspace()).split())
            if clave not in claves:
                claves[clave] = len(grupos)
                grupos.append([])
            grupos[claves[clave]].append(t)
    else:
        vectores = np.asarray([embedder.embed_query(t) for t in textos], dtype=float)
        vectores /= np.linalg.norm(vectores, axis=1, keepdims=True)
        sumas: list[np.ndarray] = []
        for texto, v in zip(textos, vectores, strict=True):
            mejor, similitud = -1, umbral
            for i, suma in enumerate(sumas):
                centroide = suma / np.linalg.norm(suma)
                if (s := float(centroide @ v)) >= similitud:
                    mejor, similitud = i, s
            if mejor < 0:
                grupos.append([texto])
                sumas.append(v.copy())
            else:
                grupos[mejor].append(texto)
                sumas[mejor] += v
    resultado = []
    for grupo in grupos:
        conteo = Counter(grupo)
        representante = max(conteo, key=lambda t: (conteo[t], -grupo.index(t)))
        ejemplos = [t for t in dict.fromkeys(grupo) if t != representante][:3]
        resultado.append(QuestionGroup(question=representante, count=len(grupo), examples=ejemplos))
    resultado.sort(key=lambda g: -g.count)  # estable: a igual conteo, orden de aparición
    return resultado[:top_n]


# --- Resumen ---------------------------------------------------------------------------------


def _latencias(turnos: pd.DataFrame) -> dict[str, Percentiles]:
    """p50/p95 por etapa. `rewrite` solo en turnos reformulados y `llm` solo en turnos
    donde se llamó al LLM para responder; el resto, en todos los turnos con dato."""
    salida = {}
    for etapa in ETAPAS:
        valores = [float(v) for v in turnos[f"{etapa}_ms"].dropna()] if len(turnos) else []
        salida[etapa] = Percentiles(
            n=len(valores), p50=percentile(valores, 50), p95=percentile(valores, 95)
        )
    return salida


def summarize(
    messages: Sequence[Message],
    settings: Settings,
    *,
    embedder: Embedder | None = None,
    since: datetime | None = None,
    source: str = "",
    now: datetime | None = None,
) -> AnalyticsSummary:
    """Calcula todas las métricas sobre `messages` (ya filtrados por `since`)."""
    turnos = turns_frame(messages, settings)
    n = len(turnos)
    conversaciones = {m.conversation_id for m in messages}
    por_conversacion = turnos.groupby("conversation_id").size() if n else pd.Series(dtype=int)
    # Conversaciones con mensajes pero sin turnos completos cuentan con 0 turnos.
    conteos = [int(por_conversacion.get(c, 0)) for c in sorted(conversaciones)]

    operativas = Operational(
        conversations=len(conversaciones),
        messages=len(messages),
        turns=n,
        turns_per_conversation=ConversationStats(
            mean=round(float(np.mean(conteos)), 2) if conteos else 0.0,
            median=float(np.median(conteos)) if conteos else 0.0,
        ),
        turns_by_day={
            str(k): int(v)
            for k, v in sorted(Counter(t.date().isoformat() for t in turnos["created_at"]).items())
        },
        turns_by_hour={
            f"{h:02d}": int(c)
            for h, c in sorted(Counter(t.hour for t in turnos["created_at"]).items())
        },
        latency_ms=_latencias(turnos),
    )

    sin_respuesta = int(turnos["no_answer"].sum()) if n else 0
    abst = turnos[turnos["no_answer_type"] == "abstencion_llm"] if n else turnos
    up = int((turnos["feedback"] == "up").sum()) if n else 0
    down = int((turnos["feedback"] == "down").sum()) if n else 0
    scores = turnos["top_score"].dropna() if n else pd.Series(dtype=float)
    calidad = Quality(
        no_answer=_count(sin_respuesta, n),
        hard_cut=_count(int((turnos["no_answer_type"] == "corte_duro").sum()) if n else 0, n),
        llm_abstention=_count(len(abst), n),
        llm_abstention_gray_zone=int(abst["gray_zone"].sum()) if len(abst) else 0,
        llm_abstention_outside_gray_zone=int((~abst["gray_zone"]).sum()) if len(abst) else 0,
        gray_zone_turns=_count(int(turnos["gray_zone"].sum()) if n else 0, n),
        with_sources=_count(int((turnos["n_sources"] > 0).sum()) if n else 0, n),
        reranker_top_score_mean=round(float(scores.mean()), 3) if len(scores) else None,
        feedback=Feedback(
            up=up,
            down=down,
            voted=up + down,
            coverage_pct=_pct(up + down, n),
            up_rate_pct=_pct(up, up + down),
        ),
    )

    top_n = settings.analytics_top_n
    urls: Counter[str] = Counter()
    for m in messages:
        if m.role == "assistant":
            urls.update(str(s["url"]) for s in m.sources if s.get("url"))
    secciones: Counter[str] = Counter()
    for url, c in urls.items():
        secciones[section_of(url)] += c
    agrupacion = (
        f"embeddings ({type(embedder).__name__}), coseno ≥ {settings.analytics_faq_similarity}"
        if embedder is not None
        else "texto normalizado (sin embedder)"
    )
    contenido = Content(
        top_urls=[RankedItem(name=u, count=c) for u, c in urls.most_common(top_n)],
        top_sections=[RankedItem(name=s, count=c) for s, c in secciones.most_common(top_n)],
        frequent_questions=group_questions(
            list(turnos["standalone_question"]) if n else [],
            embedder,
            settings.analytics_faq_similarity,
            top_n,
        ),
        content_gaps=group_questions(
            list(turnos.loc[turnos["no_answer"], "standalone_question"]) if n else [],
            embedder,
            settings.analytics_faq_similarity,
            top_n,
        ),
        grouping=agrupacion,
    )

    con_dato = turnos[turnos["rewritten"].notna()] if n else turnos
    memoria = Memory(
        multi_turn_conversations=_count(sum(c > 1 for c in conteos), len(conteos)),
        rewritten_turns=_count(int(con_dato["rewritten"].astype(bool).sum()), len(con_dato)),
        turns_with_rewrite_data=len(con_dato),
    )

    entrada = int(turnos["prompt_tokens"].sum()) if n else 0
    salida = int(turnos["completion_tokens"].sum()) if n else 0
    costo_total = (
        entrada * settings.llm_price_input_per_mtok + salida * settings.llm_price_output_per_mtok
    ) / 1e6
    costo = Cost(
        prompt_tokens=entrada,
        completion_tokens=salida,
        total_tokens=entrada + salida,
        tokens_per_turn=round((entrada + salida) / n, 1) if n else 0.0,
        estimated_cost_usd=round(costo_total, 6),
        estimated_cost_per_turn_usd=round(costo_total / n, 6) if n else 0.0,
        price_input_per_mtok=settings.llm_price_input_per_mtok,
        price_output_per_mtok=settings.llm_price_output_per_mtok,
        note=NOTA_COSTO,
    )

    resueltas = int(((~turnos["no_answer"]) & (turnos["feedback"] != "down")).sum()) if n else 0
    impacto = Impact(
        resolved_turns=resueltas,
        resolution_rate_pct=_pct(resueltas, n),
        manual_search_minutes=settings.manual_search_minutes,
        hours_saved=round(resueltas * settings.manual_search_minutes / 60, 2),
        cost_per_resolved_usd=round(costo_total / resueltas, 6) if resueltas else None,
        assumption=SUPUESTO_IMPACTO,
    )

    return AnalyticsSummary(
        meta=Meta(
            generated_at=now or datetime.now(UTC),
            since=since,
            source=source,
            demo=is_demo_source(source),
            timezone=settings.analytics_timezone,
            rerank_hard_min_score=settings.rerank_hard_min_score,
            rerank_min_score=settings.rerank_min_score,
            faq_similarity=settings.analytics_faq_similarity,
        ),
        operational=operativas,
        quality=calidad,
        content=contenido,
        memory=memoria,
        cost=costo,
        impact=impacto,
    )
