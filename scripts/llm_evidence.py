"""Evidencia de M7 con llamadas reales a Grok (pocas, para controlar el costo).

1. `preguntas`: 6 preguntas de punta a punta (reformulación → retrieval → rerank → LLM)
   con respuesta, fuentes, tokens, latencia por etapa y costo estimado.
2. `reformulacion`: sobre `eval/calibration.jsonl`, compara QUERY_REWRITE_MODE=off frente
   a always en la exactitud del umbral, la URL esperada en el top-n, los tokens y la
   latencia extra. Solo llama al LLM para reformular (30 llamadas cortas).

El costo se estima con LLM_PRICE_INPUT_PER_MTOK y LLM_PRICE_OUTPUT_PER_MTOK (USD por
millón de tokens, de https://docs.x.ai/docs/models).

Uso (Qdrant levantado e indexado, XAI_API_KEY en .env):
    python scripts/llm_evidence.py preguntas
    python scripts/llm_evidence.py reformulacion --pausa 7   # respeta el límite por minuto
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from rag_bbva.config import get_settings
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.logging_conf import configure_logging
from rag_bbva.retrieval.calibration import evaluate, load_questions

PREGUNTAS = [
    ("a1", "requisitos para crédito de vivienda", None),
    ("a2", "¿qué es un CDT?", None),
    ("a3", "¿cómo descargo un comprobante de transferencia?", None),
    ("b", "¿cuáles son los requisitos del crédito de vivienda del Banco de Bogotá?", None),
    ("c", "receta de arepas", None),
    ("d-off", "¿cuánto cobran por el 4 por mil?", "off"),
    ("d-always", "¿cuánto cobran por el 4 por mil?", "always"),
]


def _costo(prompt: int, completion: int) -> float:
    s = get_settings()
    return (prompt * s.llm_price_input_per_mtok + completion * s.llm_price_output_per_mtok) / 1e6


def preguntas(salida: Path) -> None:
    """Las 6 preguntas (7 corridas: la d va con off y con always)."""
    fabrica = ComponentFactory(get_settings())
    llm = fabrica.create_llm()
    retriever = fabrica.create_retriever()
    retriever.warm_up()
    generador = fabrica.create_answer_generator(llm)
    filas = []
    for clave, pregunta, modo in PREGUNTAS:
        reformulador = fabrica.create_query_rewriter(llm, mode=modo or "off")
        inicio = time.perf_counter()
        reformulada = reformulador.rewrite(pregunta)
        recuperacion = retriever.retrieve(reformulada.query)
        respuesta = generador.generate(pregunta, recuperacion)
        total_ms = round((time.perf_counter() - inicio) * 1000, 1)
        tokens_in = respuesta.prompt_tokens + reformulada.prompt_tokens
        tokens_out = respuesta.completion_tokens + reformulada.completion_tokens
        filas.append(
            {
                "id": clave,
                "pregunta": pregunta,
                "modo_reformulacion": modo or "off",
                "pregunta_para_buscar": reformulada.query,
                "top_score": recuperacion.top_score,
                "no_answer": recuperacion.no_answer,
                "llm_llamado": respuesta.llm_called,
                "respuesta": respuesta.text,
                "fuentes": [f.model_dump() for f in respuesta.sources],
                "tokens": {"entrada": tokens_in, "salida": tokens_out},
                "latencias_ms": {
                    "reformulacion": reformulada.latency_ms,
                    "retrieval": recuperacion.retrieval_ms,
                    "rerank": recuperacion.rerank_ms,
                    "llm": respuesta.llm_ms,
                    "total": total_ms,
                },
                "costo_usd": round(_costo(tokens_in, tokens_out), 6),
                "modelo": respuesta.model,
            }
        )
        print(json.dumps(filas[-1], ensure_ascii=False, indent=1))
    salida.write_text(json.dumps(filas, ensure_ascii=False, indent=2), encoding="utf-8")


def reformulacion(salida: Path, pausa: float = 0.0) -> None:
    """off frente a always sobre las 30 preguntas de calibración."""
    ajustes = get_settings()
    fabrica = ComponentFactory(ajustes)
    llm = fabrica.create_llm()
    retriever = fabrica.create_retriever()
    retriever.warm_up()
    resultados: dict[str, list[dict[str, object]]] = {}
    for modo in ("off", "always"):
        reformulador = fabrica.create_query_rewriter(llm, mode=modo)
        filas = []
        for p in load_questions(Path("eval/calibration.jsonl")):
            reformulada = reformulador.rewrite(p.question)
            if reformulada.used_llm and pausa:
                # El nivel gratuito limita las solicitudes por minuto: sin pausa, las
                # llamadas seguidas reciben 429 y la reformulación cae al respaldo.
                time.sleep(pausa)
            r = retriever.retrieve(reformulada.query)
            filas.append(
                {
                    "id": p.id,
                    "answerable": p.answerable,
                    "query": reformulada.query,
                    "top_score": r.top_score,
                    "expected_in_top_n": (
                        any(c.url == p.expected_url for c in r.results) if p.expected_url else None
                    ),
                    "used_llm": reformulada.used_llm,
                    "rewrite_ms": reformulada.latency_ms,
                    "tokens_in": reformulada.prompt_tokens,
                    "tokens_out": reformulada.completion_tokens,
                }
            )
        resultados[modo] = filas
    resumen = {}
    for modo, filas in resultados.items():
        pos = [f["top_score"] for f in filas if f["answerable"]]
        neg = [f["top_score"] for f in filas if not f["answerable"]]
        matriz = evaluate(ajustes.rerank_min_score, pos, neg)  # type: ignore[arg-type]
        latencias = [f["rewrite_ms"] for f in filas if f["used_llm"]] or [0.0]
        resumen[modo] = {
            "umbral": ajustes.rerank_min_score,
            "matriz": matriz.model_dump(),
            "url_esperada_en_top_n": sum(1 for f in filas if f["expected_in_top_n"]),
            "reformuladas_con_llm": sum(1 for f in filas if f["used_llm"]),
            "tokens_reformulacion": {
                "entrada": sum(f["tokens_in"] for f in filas),  # type: ignore[misc]
                "salida": sum(f["tokens_out"] for f in filas),  # type: ignore[misc]
            },
            "latencia_reformulacion_ms": {
                "p50": round(statistics.median(latencias), 1),  # type: ignore[type-var]
                "max": max(latencias),  # type: ignore[type-var]
            },
        }
        resumen[modo]["costo_reformulacion_usd"] = round(
            _costo(
                resumen[modo]["tokens_reformulacion"]["entrada"],
                resumen[modo]["tokens_reformulacion"]["salida"],
            ),
            6,
        )
    print(json.dumps(resumen, ensure_ascii=False, indent=1))
    for off, always in zip(resultados["off"], resultados["always"], strict=True):
        print(
            f"{off['id']} {off['top_score']:7.2f} → {always['top_score']:7.2f} | {always['query']}"
        )
    salida.write_text(
        json.dumps({"resumen": resumen, "detalle": resultados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    """Punto de entrada de la línea de comandos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("parte", choices=["preguntas", "reformulacion"])
    parser.add_argument("--output-dir", type=Path, default=Path("data/eval"))
    parser.add_argument(
        "--pausa", type=float, default=0.0, help="Segundos entre llamadas de reformulación."
    )
    args = parser.parse_args()
    configure_logging()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destino = args.output_dir / f"m07_{args.parte}.json"
    if args.parte == "preguntas":
        preguntas(destino)
    else:
        reformulacion(destino, args.pausa)
    print("Guardado en", destino)


if __name__ == "__main__":
    main()
