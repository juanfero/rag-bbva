"""Guion de prueba de la interfaz (M10) contra la API real, sin navegador.

Ejecuta `src/rag_bbva/ui/app.py` con `streamlit.testing.v1.AppTest` y el `ApiClient`
real apuntando a `--api-url`: es el mismo código de la UI haciendo peticiones HTTP de
verdad. Imprime lo que la pantalla muestra en cada paso (mensajes, avisos, errores,
fuentes, ID de la conversación y estado del servicio).

- `parte1`: tres conversaciones (vivienda con seguimientos, valoración y modo detalle;
  CDT y una pregunta fuera de dominio; otra entidad y una pregunta demasiado larga).
  Guarda los IDs en `--ids`.
- `parte2`: (tras reiniciar la API) una sesión nueva retoma por ID la conversación de
  vivienda y hace una pregunta más.

Uso (API levantada; gasta cupo del LLM: ~8 respuestas):
    python scripts/ui_guion.py parte1 --api-url http://127.0.0.1:8010
    python scripts/ui_guion.py parte2 --api-url http://127.0.0.1:8010
"""

import argparse
import json
import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

from rag_bbva.ui.api_client import ApiClient

APP = str(Path(__file__).resolve().parents[1] / "src" / "rag_bbva" / "ui" / "app.py")


def nueva_sesion(api_url: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=400)
    at.session_state["api_client"] = ApiClient(api_url, timeout=380)
    return at.run()


def corto(texto: str, n: int = 220) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1] + "…"


def describir(at: AppTest, paso: str) -> None:
    """Lo que se ve tras el paso: último intercambio, avisos, fuentes y estado."""
    print(f"\n### {paso}")
    if at.exception:
        print(f"EXCEPCIÓN: {[e.value for e in at.exception]}")
    cid = at.session_state["conversation_id"]
    print(f"- ID en la barra lateral: {cid or '(nueva)'}")
    mensajes = at.session_state["messages"]
    print(f"- Mensajes en pantalla: {len(mensajes)}")
    if mensajes and mensajes[-1]["role"] == "assistant":
        m = mensajes[-1]
        print(f"- Pregunta: {corto(mensajes[-2]['content'], 120)}")
        if m["no_answer"]:
            print("- Aviso amarillo: «Sin información suficiente en el sitio de Bancolombia»")
        print(f"- Respuesta: {corto(m['content'])}")
        print(f"- Fuentes ({len(m['sources'])}): " + "; ".join(s["url"] for s in m["sources"]))
        d = m.get("detail") or {}
        if d.get("timings"):
            print(
                f"- Detalle: reformulada={d.get('rewritten_query')!r} · "
                f"zona_gris={d.get('gray_zone')} · modelo={d.get('model')} · "
                f"total={d['timings'].get('total')} ms"
            )
    for e in at.error:
        print(f"- Error en rojo: {corto(e.value, 300)}")
    for e in at.expander:
        if e.label == "Detalle técnico":
            print("- Desplegable «Detalle técnico» visible (modo detalle activo)")


def boton(at: AppTest, etiqueta: str):  # type: ignore[no-untyped-def]
    """Botón por su etiqueta (la lista de conversaciones cambia los índices)."""
    return next(b for b in at.button if b.label == etiqueta)


def preguntar(at: AppTest, pregunta: str, paso: str) -> AppTest:
    inicio = time.perf_counter()
    at = at.chat_input[0].set_value(pregunta).run()
    describir(at, f"{paso} ({time.perf_counter() - inicio:.1f} s)")
    return at


def parte1(api_url: str, ids: Path) -> None:
    at = nueva_sesion(api_url)
    estado = [
        m.value.split("**")[1] + (" ok" if "🟢" in m.value else " CAÍDO")
        for m in at.sidebar.markdown
        if "**" in m.value
    ]
    print(f"## Pantalla inicial\n- Aviso: {at.info[0].value}\n- Estado: {estado}")

    print("\n## Conversación A — crédito de vivienda (seguimientos, 👍 y modo detalle)")
    at = preguntar(at, "¿Qué es el crédito de vivienda de Bancolombia?", "A1")
    at = preguntar(at, "¿y cuáles son los requisitos?", "A2")
    at = at.toggle[0].set_value(True).run()
    at = preguntar(
        at, "¿y qué costos adicionales tiene, aparte de las cuotas?", "A3 con modo detalle"
    )
    respuesta = at.session_state["messages"][-1]["message_id"]
    at = at.button(key=f"up_{respuesta}").click().run()
    deshabilitados = all(at.button(key=f"{v}_{respuesta}").disabled for v in ("up", "down"))
    voto = at.session_state["messages"][-1]["feedback"]
    print(f"\n### A3 👍 → feedback={voto} · botones deshabilitados={deshabilitados}")
    conversacion_a = at.session_state["conversation_id"]

    print("\n## Conversación B — CDT y una pregunta fuera de dominio")
    at = boton(at, "Nueva conversación").click().run()
    nuevo, cantidad = at.session_state["conversation_id"], len(at.session_state["messages"])
    print(f"- «Nueva conversación» → ID {nuevo}, mensajes {cantidad}")
    at = preguntar(at, "¿Qué es un CDT?", "B1")
    at = preguntar(at, "dame una receta de arepas de queso", "B2 fuera de dominio")
    conversacion_b = at.session_state["conversation_id"]

    print("\n## Conversación C — otra entidad y validación")
    at = boton(at, "Nueva conversación").click().run()
    at = preguntar(at, "¿Cómo abro una cuenta de ahorros en el Banco de Bogotá?", "C1 otra entidad")
    at = preguntar(at, "x" * 1001, "C2 pregunta de 1001 caracteres")
    conversacion_c = at.session_state["conversation_id"]

    ids.write_text(json.dumps({"A": conversacion_a, "B": conversacion_b, "C": conversacion_c}))
    print(f"\nIDs: A={conversacion_a} B={conversacion_b} C={conversacion_c}")


def parte2(api_url: str, ids: Path) -> None:
    conversaciones = json.loads(ids.read_text())
    at = nueva_sesion(api_url)
    lista = [b.label for b in at.sidebar.button if b.key and b.key.startswith("conv_")]
    print(f"## Sesión nueva tras reiniciar la API\n- Lista «Retomar»: {lista}")
    at.text_input[0].input(conversaciones["A"])
    at = boton(at, "Retomar por ID").click().run()
    mensajes = at.session_state["messages"]
    assert at.session_state["conversation_id"] == conversaciones["A"], "no se retomó A"
    print(
        f"- Retomar por ID {conversaciones['A']} → {len(mensajes)} mensajes; valoraciones: "
        f"{[m.get('feedback') for m in mensajes if m['role'] == 'assistant']}"
    )
    at = preguntar(
        at, "¿y qué porcentaje del valor de la vivienda me financian?", "A4 tras el reinicio"
    )
    at = at.toggle[0].set_value(True).run()
    describir(at, "A4 con modo detalle")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("parte", choices=["parte1", "parte2"])
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ids", type=Path, default=Path("data/eval/m10_guion_ids.json"))
    args = parser.parse_args()
    args.ids.parent.mkdir(parents=True, exist_ok=True)
    (parte1 if args.parte == "parte1" else parte2)(args.api_url, args.ids)


if __name__ == "__main__":
    main()
