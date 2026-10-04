"""Página "Métricas" (M11): analítica del historial, consumida por HTTP desde
`GET /analytics/summary`. Como el resto de la interfaz, no importa el núcleo."""

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from rag_bbva.exceptions import ApiClientError
from rag_bbva.ui.render import AVISO, friendly_error
from rag_bbva.ui.state import api_client

AVISO_DEMO = (
    "**Datos de demostración.** Estas métricas salen de conversaciones generadas por "
    "`scripts/seed_conversations.py` sobre `demo.db`, por el pipeline real; los votos 👍/👎 "
    "los aplicó el script. No representan uso real."
)


def _tabla(filas: list[dict[str, Any]], columnas: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [{v: f.get(k) for k, v in columnas.items()} for f in filas], columns=list(columnas.values())
    )


def _barras(conteos: dict[str, int], indice: str) -> None:
    if conteos:
        st.bar_chart(pd.DataFrame({"turnos": conteos}).rename_axis(indice))
    else:
        st.caption("Sin datos en el periodo.")


def _pct(x: dict[str, Any]) -> str:
    return f"{x['pct']:.1f} %"


def main() -> None:
    """Página de métricas."""
    st.set_page_config(page_title="Métricas", page_icon="📊", layout="wide")
    st.title("Métricas del historial")
    st.info(AVISO, icon="⚠️")

    filtrar = st.sidebar.checkbox("Filtrar por fecha")
    desde: date | None = st.sidebar.date_input("Desde", value=date.today()) if filtrar else None  # type: ignore[assignment]

    try:
        r = api_client().analytics_summary(desde)
    except ApiClientError as exc:
        st.error(f"No se pudieron cargar las métricas. {friendly_error(exc)}")
        return

    meta, o, q, c = r["meta"], r["operational"], r["quality"], r["content"]
    m, k, i = r["memory"], r["cost"], r["impact"]
    if meta["demo"]:
        st.warning(AVISO_DEMO, icon="🧪")
    st.caption(
        f"Fuente: {meta['source']} · desde: {meta['since'] or 'todo el historial'} · "
        f"zona horaria: {meta['timezone']}"
    )

    if o["turns"] == 0:
        st.info("Todavía no hay conversaciones en el periodo.")

    st.subheader("Resumen")
    fila1 = st.columns(4)
    fila1[0].metric("Conversaciones", o["conversations"])
    fila1[1].metric("Turnos (preguntas respondidas)", o["turns"])
    fila1[2].metric("Sin información suficiente", _pct(q["no_answer"]))
    fila1[3].metric("Con al menos una fuente", _pct(q["with_sources"]))
    fila2 = st.columns(4)
    fb = q["feedback"]
    fila2[0].metric(
        "👍 sobre las votadas", f"{fb['up_rate_pct']:.1f} %", help=f"{fb['voted']} votos"
    )
    fila2[1].metric("Tasa de resolución (estimada)", f"{i['resolution_rate_pct']:.1f} %")
    fila2[2].metric("Horas ahorradas (estimadas)", f"{i['hours_saved']:.2f} h")
    fila2[3].metric("Costo estimado", f"US$ {k['estimated_cost_usd']:.4f}")

    st.subheader("Operación")
    izquierda, derecha = st.columns(2)
    with izquierda:
        st.markdown("**Turnos por día**")
        _barras(o["turns_by_day"], "día")
    with derecha:
        st.markdown(f"**Turnos por hora** ({meta['timezone']})")
        _barras(o["turns_by_hour"], "hora")
    st.markdown("**Latencia por etapa (ms)**")
    latencias = pd.DataFrame(
        [
            {"etapa": etapa, "n": v["n"], "p50": v["p50"], "p95": v["p95"]}
            for etapa, v in o["latency_ms"].items()
        ]
    )
    st.dataframe(latencias, hide_index=True)
    st.caption(
        f"Turnos por conversación: media {o['turns_per_conversation']['mean']} · mediana "
        f"{o['turns_per_conversation']['median']}. Percentiles por rango más cercano; "
        "`rewrite` solo en turnos reformulados y `llm` solo cuando se llamó al LLM."
    )

    st.subheader("Calidad")
    st.markdown(
        f"- **Sin información:** {q['no_answer']['count']} ({_pct(q['no_answer'])}) = "
        f"corte duro sin LLM (score < {meta['rerank_hard_min_score']}) "
        f"{q['hard_cut']['count']} ({_pct(q['hard_cut'])}) + abstención del LLM "
        f"{q['llm_abstention']['count']} ({_pct(q['llm_abstention'])}; "
        f"{q['llm_abstention_gray_zone']} en zona gris, "
        f"{q['llm_abstention_outside_gray_zone']} fuera)\n"
        f"- **Turnos en zona gris:** {q['gray_zone_turns']['count']} "
        f"({_pct(q['gray_zone_turns'])})\n"
        f"- **Score medio del reranker (#1):** {q['reranker_top_score_mean']}\n"
        f"- **Valoraciones:** 👍 {fb['up']} · 👎 {fb['down']} · cobertura "
        f"{fb['coverage_pct']:.1f} % de los turnos"
    )

    st.subheader("Contenido")
    izquierda, derecha = st.columns(2)
    with izquierda:
        st.markdown("**Secciones más citadas**")
        secciones = {s["name"]: s["count"] for s in c["top_sections"]}
        if secciones:
            st.bar_chart(pd.DataFrame({"citas": secciones}).rename_axis("sección"))
        else:
            st.caption("Sin citas en el periodo.")
    with derecha:
        st.markdown("**URLs más citadas**")
        st.dataframe(_tabla(c["top_urls"], {"name": "URL", "count": "citas"}), hide_index=True)
    columnas_grupo = {"question": "pregunta", "count": "veces", "examples": "ejemplos"}
    st.markdown(f"**Preguntas frecuentes** (agrupadas por {c['grouping']})")
    st.dataframe(_tabla(c["frequent_questions"], columnas_grupo), hide_index=True)
    st.markdown("**Brechas de contenido**: preguntas sin respuesta, oportunidades de contenido")
    brechas = _tabla(c["content_gaps"], columnas_grupo)
    st.dataframe(brechas, hide_index=True)
    st.download_button(
        "Descargar brechas (CSV)", brechas.to_csv(index=False), "brechas_de_contenido.csv"
    )
    st.caption(
        "Los números de 6 o más dígitos (cédulas, cuentas, teléfonos) se muestran como [número]."
    )

    st.subheader("Memoria, costo e impacto")
    izquierda, derecha = st.columns(2)
    with izquierda:
        st.markdown(
            f"- **Conversaciones de más de un turno:** "
            f"{m['multi_turn_conversations']['count']} ({_pct(m['multi_turn_conversations'])})\n"
            f"- **Turnos con pregunta reformulada:** {m['rewritten_turns']['count']} "
            f"({_pct(m['rewritten_turns'])} de {m['turns_with_rewrite_data']})\n"
            f"- **Tokens:** {k['prompt_tokens']:,} entrada + {k['completion_tokens']:,} salida "
            f"({k['tokens_per_turn']} por turno)"
        )
        st.caption(k["note"])
    with derecha:
        costo = i["cost_per_resolved_usd"]
        st.markdown(
            f"- **Consultas resueltas:** {i['resolved_turns']} "
            f"(tasa {i['resolution_rate_pct']:.1f} %)\n"
            f"- **Horas de búsqueda manual ahorradas:** {i['hours_saved']} = "
            f"{i['resolved_turns']} consultas · {i['manual_search_minutes']} min / 60\n"
            f"- **Costo por consulta resuelta:** " + ("—" if costo is None else f"US$ {costo:.6f}")
        )
        st.caption(f"Supuesto: {i['assumption']}")


main()
