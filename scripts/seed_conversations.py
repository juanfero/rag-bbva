"""Historial de demostración para la analítica (M11), generado por el pipeline REAL.

Corre conversaciones guionizadas por `RAGService` (reformulación, Qdrant, reranker,
umbral doble y LLM reales) contra una base separada, `data/history/demo.db`, para que la
analítica tenga datos sin inventar métricas: cada latencia, score, token y abstención
sale de la ejecución. El historial real (`HISTORY_DB_PATH`) no se toca.

Lo único que pone el script son los **votos 👍/👎** (`VOTOS`): se aplican por el mismo
repositorio que usa la API y se declaran como datos de demostración. La analítica, la CLI
y la interfaz marcan la base como "Datos de demostración" por su nombre (`demo.db`).

Gasta cupo del LLM (~1 llamada por turno, más la reformulación de cada pregunta de
seguimiento) y hace una pausa entre turnos por el límite por minuto de Gemini.

Uso (Qdrant levantado e indexado, GEMINI_API_KEY en .env):
    python scripts/seed_conversations.py --pausa 4
    python scripts/seed_conversations.py --recrear     # borra demo.db y empieza de cero
"""

import argparse
import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any

from rag_bbva.config import get_settings
from rag_bbva.exceptions import RagBbvaError
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.llm.provider import FallbackLLMProvider, LLMProvider
from rag_bbva.logging_conf import configure_logging
from rag_bbva.memory.sql_repository import SqlAlchemyConversationRepository

DEMO_DB = Path("data/history/demo.db")

# 12 conversaciones: multiturno, fuera de dominio, otra entidad, una casi duplicada (para la
# agrupación de preguntas frecuentes) y una con un número de cédula (para el enmascarado).
CONVERSACIONES: list[list[str]] = [
    [
        "¿Qué es el crédito de vivienda de Bancolombia?",
        "¿y cuáles son los requisitos?",
        "¿y qué porcentaje del valor de la vivienda me financian?",
    ],
    ["¿Qué es un CDT?", "¿y qué tasa tiene a 90 días para 5 millones?"],
    ["¿Cómo bloqueo mi tarjeta de crédito?", "¿y si es la tarjeta débito?"],
    ["¿Cuánto cobran por el 4 por mil?", "¿cómo marco mi cuenta como exenta?"],
    ["¿Cuánto cuesta un retiro en sucursal física con el Plan Cero?"],
    ["¿Cómo descargo un comprobante de transferencia?"],
    ["dame una receta de arepas de queso"],
    ["¿quién ganó el Mundial de fútbol de 2022?"],
    ["¿Cómo abro una cuenta de ahorros en el Banco de Bogotá?"],
    ["que es un cdt"],
    ["mi cédula es 1020345678, ¿puedo abrir un CDT?"],
    ["¿Qué es el leasing habitacional?", "¿y qué costos adicionales tiene?"],
]

# Votos que aplica el script: (conversación, turno) → valor. Declarados como demostración.
VOTOS: dict[tuple[int, int], str] = {
    (0, 2): "up",
    (1, 1): "up",
    (2, 0): "up",
    (2, 1): "down",
    (4, 0): "up",
    (5, 0): "up",
    (8, 0): "down",
    (11, 1): "up",
}


class Contador:
    """Cuenta las llamadas a `complete` de un proveedor, incluidas las que fallan."""

    def __init__(self, proveedor: LLMProvider) -> None:
        self.llamadas = 0
        self.fallidas = 0
        original = proveedor.complete

        def contar(*args: Any, **kwargs: Any) -> Any:
            self.llamadas += 1
            try:
                return original(*args, **kwargs)
            except Exception:
                self.fallidas += 1
                raise

        proveedor.complete = contar  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEMO_DB)
    parser.add_argument("--pausa", type=float, default=4.0, help="segundos entre turnos")
    parser.add_argument("--recrear", action="store_true", help="borra la base antes de empezar")
    parser.add_argument("--reporte", type=Path, default=Path("data/eval/m11_seed.json"))
    args = parser.parse_args()
    configure_logging()
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    if "demo" not in args.db.name:
        parser.error("La base de demostración debe llamarse *demo* (la analítica la marca así).")
    if args.db.exists():
        if not args.recrear:
            parser.error(f"{args.db} ya existe; use --recrear para empezar de cero.")
        args.db.unlink()

    ajustes = get_settings()
    fabrica = ComponentFactory(ajustes)
    llm = fabrica.create_llm()
    contadores: dict[str, Contador] = {}
    if isinstance(llm, FallbackLLMProvider):
        contadores = {
            f"principal ({llm.primary.model})": Contador(llm.primary),
            f"respaldo ({llm.fallback.model})": Contador(llm.fallback),
        }
    else:
        contadores = {llm.model: Contador(llm)}
    repo = SqlAlchemyConversationRepository.from_path(args.db)
    servicio = fabrica.create_rag_service(repository=repo, llm=llm)
    servicio.warm_up()

    turnos: list[dict[str, Any]] = []
    errores: list[str] = []
    inicio = time.perf_counter()
    for i, preguntas in enumerate(CONVERSACIONES):
        conversacion = None
        for j, pregunta in enumerate(preguntas):
            try:
                r = servicio.ask(conversacion, pregunta)
            except RagBbvaError as exc:
                errores.append(f"C{i + 1}.{j + 1} {pregunta!r}: {exc.message}")
                print(f"C{i + 1}.{j + 1} ERROR {exc.message}")
                time.sleep(args.pausa)
                continue
            conversacion = r.conversation_id
            if voto := VOTOS.get((i, j)):
                repo.set_feedback(r.message_id, voto)  # type: ignore[arg-type]
            turnos.append(
                {
                    "turno": f"C{i + 1}.{j + 1}",
                    "pregunta": pregunta,
                    "no_answer": r.no_answer,
                    "gray_zone": r.gray_zone,
                    "modelo": r.model,
                    "voto_del_script": voto,
                    "total_ms": r.timings.total,
                    "rewritten_query": r.rewritten_query,
                }
            )
            print(
                f"C{i + 1}.{j + 1} {'SIN INFO' if r.no_answer else 'responde'} "
                f"{'(zona gris) ' if r.gray_zone else ''}{r.model or 'sin LLM'} "
                f"{r.timings.total / 1000:.1f} s{' · voto ' + voto if voto else ''} · {pregunta}"
            )
            time.sleep(args.pausa)

    reporte = {
        "base": str(args.db),
        "conversaciones_guion": len(CONVERSACIONES),
        "turnos_guardados": len(turnos),
        "errores": errores,
        "votos_del_script": {f"C{i + 1}.{j + 1}": v for (i, j), v in VOTOS.items()},
        "llamadas_al_llm": {
            nombre: {"llamadas": c.llamadas, "fallidas": c.fallidas}
            for nombre, c in contadores.items()
        },
        "respondidas_por_modelo": dict(Counter(t["modelo"] or "sin LLM" for t in turnos)),
        "duracion_s": round(time.perf_counter() - inicio, 1),
        "turnos": turnos,
    }
    args.reporte.parent.mkdir(parents=True, exist_ok=True)
    args.reporte.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), "utf-8")
    resumen = {k: v for k, v in reporte.items() if k != "turnos"}
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    repo.close()


if __name__ == "__main__":
    main()
