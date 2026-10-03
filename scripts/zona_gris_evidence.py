"""Evidencia del umbral doble (M9, ADR-016) con llamadas reales al LLM.

Pasa las 30 preguntas de `eval/calibration.jsonl` por recuperación → reranking → umbral
doble → generación (sin historial y sin guardar nada) y reporta por pregunta:
- la zona del umbral: `dura` (debajo de RERANK_HARD_MIN_SCORE, sin LLM), `gris` (entre
  ambos umbrales, con LLM) o `normal` (encima de RERANK_MIN_SCORE, con LLM);
- si el LLM respondió o se abstuvo con [SIN_INFO], qué modelo respondió y los tokens;
- si la decisión final (responder / "sin información") es correcta.

Al final cuenta las llamadas al LLM, las que cayeron en la zona gris (las "extra" frente
al umbral único de M6) y cuántas respondió el modelo de respaldo.

Uso (Qdrant levantado e indexado, GEMINI_API_KEY en .env):
    python scripts/zona_gris_evidence.py --pausa 4    # respeta el límite por minuto
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from rag_bbva.config import get_settings
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.llm.provider import FallbackLLMProvider, LLMProvider
from rag_bbva.logging_conf import configure_logging
from rag_bbva.retrieval.calibration import load_questions


class Contador:
    """Cuenta las llamadas a `complete` de un proveedor (también las que fallan)."""

    def __init__(self, proveedor: LLMProvider) -> None:
        self.llamadas = 0
        original = proveedor.complete

        def contar(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.llamadas += 1
            return original(*args, **kwargs)

        proveedor.complete = contar  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pausa", type=float, default=4.0, help="segundos entre llamadas")
    parser.add_argument("--salida", type=Path, default=Path("data/eval/m09_zona_gris.json"))
    args = parser.parse_args()
    configure_logging()

    ajustes = get_settings()
    fabrica = ComponentFactory(ajustes)
    llm = fabrica.create_llm()
    contadores = {}
    if isinstance(llm, FallbackLLMProvider):
        contadores = {"principal": Contador(llm.primary), "respaldo": Contador(llm.fallback)}
    retriever = fabrica.create_retriever()
    retriever.warm_up()
    generador = fabrica.create_answer_generator(llm)

    filas = []
    for q in load_questions(Path("eval/calibration.jsonl")):
        recuperacion = retriever.retrieve(q.question)
        zona = (
            "dura" if recuperacion.hard_no_answer
            else "gris" if recuperacion.gray_zone
            else "normal"
        )  # fmt: skip
        respuesta = generador.generate(q.question, recuperacion)
        correcta = respuesta.no_answer != q.answerable
        filas.append({
            "id": q.id, "pregunta": q.question, "respondible": q.answerable,
            "categoria": q.category, "top_score": recuperacion.top_score, "zona": zona,
            "llm_llamado": respuesta.llm_called, "abstencion_llm": respuesta.abstained,
            "no_answer": respuesta.no_answer, "decision_correcta": correcta,
            "modelo": respuesta.model, "tokens_entrada": respuesta.prompt_tokens,
            "tokens_salida": respuesta.completion_tokens, "llm_ms": respuesta.llm_ms,
            "respuesta": respuesta.text,
        })  # fmt: skip
        marca = (
            "ABSTIENE"
            if respuesta.abstained
            else ("responde" if respuesta.llm_called else "sin LLM")
        )
        print(
            f"{q.id} {'R' if q.answerable else 'N'} {recuperacion.top_score:6.2f} {zona:6s} "
            f"{marca:8s} {'OK ' if correcta else 'MAL'} {respuesta.model or '-':22s} "
            f"{q.question[:55]}"
        )
        if respuesta.llm_called:
            time.sleep(args.pausa)

    por_zona = Counter(f["zona"] for f in filas)
    resumen = {
        "umbral_duro": ajustes.rerank_hard_min_score,
        "umbral": ajustes.rerank_min_score,
        "modelo_configurado": ajustes.llm_model,
        "modelo_respaldo": ajustes.llm_fallback_model,
        "por_zona": dict(por_zona),
        "zona_gris": {
            "respondibles": sum(f["zona"] == "gris" and f["respondible"] for f in filas),
            "no_respondibles": sum(f["zona"] == "gris" and not f["respondible"] for f in filas),
            "correctas": sum(f["zona"] == "gris" and f["decision_correcta"] for f in filas),
        },
        "decisiones_correctas": sum(f["decision_correcta"] for f in filas),
        "total": len(filas),
        "llamadas_generacion": sum(f["llm_llamado"] for f in filas),
        "llamadas_extra_zona_gris": por_zona.get("gris", 0),
        "respondidas_por_modelo": dict(Counter(f["modelo"] for f in filas if f["modelo"])),
        "peticiones_por_proveedor": {k: c.llamadas for k, c in contadores.items()},
        "abstenciones_llm": sum(f["abstencion_llm"] for f in filas),
        "tokens": {
            "entrada": sum(f["tokens_entrada"] for f in filas),
            "salida": sum(f["tokens_salida"] for f in filas),
        },
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(
        json.dumps({"resumen": resumen, "filas": filas}, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    print(f"Detalle: {args.salida}")


if __name__ == "__main__":
    main()
