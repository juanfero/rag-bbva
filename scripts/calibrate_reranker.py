"""Calibra el umbral de "sin información suficiente" (RERANK_MIN_SCORE) — M6.

Corre cada pregunta de `eval/calibration.jsonl` por el retriever real (Qdrant +
cross-encoder), toma el score del reranker del #1 y elige el umbral que mejor separa
las preguntas respondibles de las no respondibles. También mide latencias y si la URL
esperada aparece en el top-n.

Uso (con Qdrant levantado e indexado):
    python scripts/calibrate_reranker.py [--output data/eval/calibration_report.json]
"""

import argparse
import json
import statistics
from pathlib import Path

from rag_bbva.config import get_settings
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.logging_conf import configure_logging
from rag_bbva.retrieval.calibration import best_threshold, evaluate, load_questions


def _percentil(valores: list[float], q: float) -> float:
    ordenados = sorted(valores)
    indice = max(0, min(len(ordenados) - 1, round(q * len(ordenados) + 0.5) - 1))
    return ordenados[indice]


def main() -> None:
    """Punto de entrada de la línea de comandos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path, default=Path("eval/calibration.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/eval/calibration_report.json"))
    args = parser.parse_args()
    configure_logging()

    retriever = ComponentFactory(get_settings()).create_retriever(rerank=True)
    retriever.warm_up()  # la carga de los modelos no cuenta como latencia
    retriever.retrieve("calentamiento")  # primera consulta: inicializaciones de torch

    filas = []
    for pregunta in load_questions(args.questions):
        resultado = retriever.retrieve(pregunta.question)
        top = resultado.results[0] if resultado.results else None
        filas.append(
            {
                "id": pregunta.id,
                "question": pregunta.question,
                "answerable": pregunta.answerable,
                "category": pregunta.category,
                "top1_rerank": resultado.top_score,
                "top1_cosine": top.cosine_score if top else None,
                "top1_url": top.url if top else None,
                "expected_in_top_n": (
                    any(c.url == pregunta.expected_url for c in resultado.results)
                    if pregunta.expected_url
                    else None
                ),
                "retrieval_ms": resultado.retrieval_ms,
                "rerank_ms": resultado.rerank_ms,
            }
        )

    positivos = [f["top1_rerank"] for f in filas if f["answerable"]]
    negativos = [f["top1_rerank"] for f in filas if not f["answerable"]]
    elegido = best_threshold(positivos, negativos)
    coseno = best_threshold(
        [f["top1_cosine"] for f in filas if f["answerable"]],
        [f["top1_cosine"] for f in filas if not f["answerable"]],
    )
    latencias = {
        etapa: {
            "p50": round(statistics.median([f[etapa] for f in filas]), 1),
            "p95": round(_percentil([f[etapa] for f in filas], 0.95), 1),
        }
        for etapa in ("retrieval_ms", "rerank_ms")
    }
    reporte = {
        "threshold": elegido.model_dump(),
        "cosine_threshold_for_comparison": coseno.model_dump(),
        "threshold_zero": evaluate(0.0, positivos, negativos).model_dump(),
        "latency_ms": latencias,
        "rows": filas,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")

    for f in sorted(filas, key=lambda f: -(f["top1_rerank"] or 0)):
        marca = "R" if f["answerable"] else "N"
        print(
            f"{marca} {f['top1_rerank']:7.3f} cos {f['top1_cosine']:.3f} "
            f"{f['category']:<20} {f['question'][:60]:<60} {f['expected_in_top_n']}"
        )
    print("Umbral (reranker):", elegido.model_dump())
    print("Umbral (coseno, comparación):", coseno.model_dump())
    print("Latencias:", latencias)
    print("Reporte:", args.output)


if __name__ == "__main__":
    main()
