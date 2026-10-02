"""Calibración del umbral de "sin información suficiente" (M6).

Con los scores del reranker (top-1) de preguntas respondibles y no respondibles, elige
el umbral que más aciertos da: una respondible acierta si su score ≥ umbral y una no
respondible si queda por debajo. Ante empates de aciertos, se elige el punto medio del
hueco más ancho entre scores, para dejar el máximo margen a ambos lados.
"""

import json
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

from pydantic import BaseModel

from rag_bbva.exceptions import RetrievalError


class CalibrationQuestion(BaseModel):
    """Una pregunta de `eval/calibration.jsonl`."""

    id: str
    question: str
    answerable: bool
    category: str
    expected_url: str | None = None


class ThresholdChoice(BaseModel):
    """Umbral elegido y su matriz de aciertos y errores."""

    threshold: float
    true_positive: int  # respondible y supera el umbral
    false_negative: int  # respondible pero queda por debajo
    true_negative: int  # no respondible y queda por debajo
    false_positive: int  # no respondible pero supera el umbral
    accuracy: float
    margin: float  # ancho del hueco entre scores en el que cae el umbral


def load_questions(path: Path) -> list[CalibrationQuestion]:
    """Lee el archivo de calibración (JSONL)."""
    if not path.exists():
        raise RetrievalError("No existe el archivo de calibración", detail=str(path))
    return [
        CalibrationQuestion.model_validate(json.loads(linea))
        for linea in path.read_text("utf-8").splitlines()
        if linea.strip()
    ]


def evaluate(
    threshold: float, positives: Sequence[float], negatives: Sequence[float]
) -> ThresholdChoice:
    """Matriz de aciertos de un umbral dado."""
    tp = sum(s >= threshold for s in positives)
    tn = sum(s < threshold for s in negatives)
    total = len(positives) + len(negatives)
    return ThresholdChoice(
        threshold=threshold,
        true_positive=tp,
        false_negative=len(positives) - tp,
        true_negative=tn,
        false_positive=len(negatives) - tn,
        accuracy=round((tp + tn) / total, 4) if total else 0.0,
        margin=0.0,
    )


def best_threshold(positives: Sequence[float], negatives: Sequence[float]) -> ThresholdChoice:
    """Umbral con más aciertos; a igualdad, el del hueco más ancho entre scores."""
    if not positives or not negatives:
        raise RetrievalError("Se necesitan scores de ambos grupos para calibrar")
    valores = sorted(set(positives) | set(negatives))
    # Candidatos: puntos medios entre scores consecutivos y los extremos.
    candidatos = [(valores[0] - 1.0, 0.0), (valores[-1] + 1.0, 0.0)]
    candidatos += [((a + b) / 2, b - a) for a, b in pairwise(valores)]
    mejor: ThresholdChoice | None = None
    for umbral, hueco in candidatos:
        opcion = evaluate(round(umbral, 4), positives, negatives).model_copy(
            update={"margin": round(hueco, 4)}
        )
        if (
            mejor is None
            or opcion.accuracy > mejor.accuracy
            or (opcion.accuracy == mejor.accuracy and opcion.margin > mejor.margin)
        ):
            mejor = opcion
    if mejor is None:  # imposible: siempre hay al menos los dos extremos
        raise RetrievalError("No se pudo elegir un umbral")
    return mejor
