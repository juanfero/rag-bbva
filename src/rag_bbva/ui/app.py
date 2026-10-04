"""Interfaz conversacional con Streamlit (M10).

Consume la API REST (M9) por HTTP con `ApiClient`; no importa el núcleo del sistema.
Uso: `python -m rag_bbva.cli ui` (o `streamlit run src/rag_bbva/ui/app.py`) con la API
levantada en `API_BASE_URL`.

Estado de la sesión (`st.session_state`):
- `api_client`: cliente de la API (los tests ponen aquí uno falso antes de ejecutar);
- `conversation_id`: conversación actual (`None` = la próxima pregunta abre una nueva);
- `messages`: mensajes mostrados, cada uno un dict con `role`, `content`, `sources`,
  `no_answer`, `message_id`, `feedback` y `detail` (para el modo detalle);
- `error`: último error para mostrar tras recargar.
"""

from typing import Any

import streamlit as st

from rag_bbva.config import get_settings
from rag_bbva.exceptions import ApiClientError
from rag_bbva.ui.api_client import ApiClient, ChatReply, ConversationDetail
from rag_bbva.ui.render import (
    AVISO,
    SIN_INFO_TITULO,
    detail_lines,
    friendly_error,
    health_lines,
    link_citations,
    shorten,
    source_lines,
)


def _cliente() -> ApiClient:
    if "api_client" not in st.session_state:
        ajustes = get_settings()
        st.session_state.api_client = ApiClient(
            ajustes.api_base_url, timeout=ajustes.ui_request_timeout_seconds
        )
    return st.session_state.api_client  # type: ignore[no-any-return]


def _iniciar_estado() -> None:
    st.session_state.setdefault("conversation_id", None)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("error", None)


def _mensaje_de_respuesta(r: ChatReply) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": r.answer,
        "sources": [s.model_dump() for s in r.sources],
        "no_answer": r.no_answer,
        "message_id": r.message_id,
        "feedback": None,
        "detail": {
            "rewritten_query": r.rewritten_query,
            "gray_zone": r.gray_zone,
            "timings": r.timings.model_dump(),
            "tokens": r.tokens.model_dump(),
            "model": r.model,
        },
    }


def _mensajes_del_historial(detalle: ConversationDetail) -> list[dict[str, Any]]:
    mensajes = []
    for m in detalle.messages:
        metricas = m.metrics
        mensajes.append(
            {
                "role": m.role,
                "content": m.content,
                "sources": m.sources,
                "no_answer": bool(metricas.get("no_answer")),
                "message_id": m.id,
                "feedback": m.feedback,
                "detail": {
                    "timings": {"total": metricas.get("total_ms")}
                    if metricas.get("total_ms") is not None
                    else None,
                    "tokens": {
                        "prompt": metricas.get("prompt_tokens") or 0,
                        "completion": metricas.get("completion_tokens") or 0,
                    }
                    if metricas.get("prompt_tokens") is not None
                    else None,
                    "top_score": metricas.get("top_score"),
                    "model": None,
                },
            }
        )
    return mensajes


# --- Acciones (callbacks) ------------------------------------------------------------


def _nueva_conversacion() -> None:
    st.session_state.conversation_id = None
    st.session_state.messages = []
    st.session_state.error = None


def _retomar(conversation_id: str) -> None:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return
    try:
        detalle = _cliente().get_conversation(conversation_id)
    except ApiClientError as exc:
        st.session_state.error = friendly_error(exc)
        return
    st.session_state.conversation_id = detalle.conversation.id
    st.session_state.messages = _mensajes_del_historial(detalle)
    st.session_state.error = None


def _votar(indice: int, valor: str) -> None:
    mensaje = st.session_state.messages[indice]
    try:
        _cliente().send_feedback(mensaje["message_id"], valor)  # type: ignore[arg-type]
    except ApiClientError as exc:
        st.session_state.error = f"No se pudo guardar la valoración. {friendly_error(exc)}"
        return
    mensaje["feedback"] = valor


# --- Vista ---------------------------------------------------------------------------


def _barra_lateral() -> bool:
    """Dibuja la barra lateral y devuelve si el modo detalle está activo."""
    with st.sidebar:
        st.header("Conversación")
        st.button(
            "Nueva conversación",
            icon=":material/add:",
            on_click=_nueva_conversacion,
            width="stretch",
        )
        actual = st.session_state.conversation_id
        st.caption("ID de la conversación actual")
        st.code(actual or "(nueva: se crea con la primera pregunta)", language=None)

        st.subheader("Retomar")
        try:
            conversaciones = _cliente().list_conversations(limit=15)
        except ApiClientError as exc:
            conversaciones = []
            st.caption(f"No se pudo cargar la lista: {friendly_error(exc)}")
        for c in conversaciones:
            titulo = shorten(c.title or "(sin título)", 40)
            st.button(
                # La fecha distingue conversaciones con la misma primera pregunta.
                f"{'▶ ' if c.id == actual else ''}{titulo} · {c.updated_at:%d/%m %H:%M}",
                key=f"conv_{c.id}",
                on_click=_retomar,
                args=(c.id,),
                help=f"{c.id} · {c.updated_at:%Y-%m-%d %H:%M} UTC",
                width="stretch",
            )
        with st.form("retomar_por_id", clear_on_submit=True, border=False):
            id_manual = st.text_input("…o pegue un ID de conversación")
            if st.form_submit_button("Retomar por ID"):
                _retomar(id_manual)
                st.rerun()

        st.subheader("Estado del servicio")
        try:
            salud = _cliente().health()
            for nombre, ok, detalle in health_lines(salud):
                st.markdown(f"{'🟢' if ok else '🔴'} **{nombre}**  \n{detalle}")
        except ApiClientError as exc:
            st.markdown(f"🔴 **API**  \n{exc.message}")

        st.divider()
        return st.toggle("Modo detalle", help="Muestra la pregunta reformulada, tiempos y modelo")


def _dibujar_mensaje(indice: int, m: dict[str, Any], detalle: bool) -> None:
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["content"].replace("$", r"\$"))
            return
        if m["no_answer"]:
            st.warning(f"**{SIN_INFO_TITULO}**", icon="🔎")
        st.markdown(link_citations(m["content"], m["sources"]))
        if m["sources"]:
            with st.expander(f"Fuentes ({len(m['sources'])})"):
                for linea in source_lines(m["sources"]):
                    st.markdown(linea)
        if detalle and m.get("detail"):
            with st.expander("Detalle técnico", expanded=True):
                for linea in detail_lines(m["detail"]):
                    st.markdown(linea)
        votado = m.get("feedback")
        izquierda, derecha, _ = st.columns([1, 1, 8])
        izquierda.button(
            "👍",
            key=f"up_{m['message_id']}",
            on_click=_votar,
            args=(indice, "up"),
            disabled=votado is not None,
            type="primary" if votado == "up" else "secondary",
            help="Respuesta útil",
        )
        derecha.button(
            "👎",
            key=f"down_{m['message_id']}",
            on_click=_votar,
            args=(indice, "down"),
            disabled=votado is not None,
            type="primary" if votado == "down" else "secondary",
            help="Respuesta no útil",
        )


def _preguntar(pregunta: str) -> None:
    with st.chat_message("user"):
        st.markdown(pregunta.replace("$", r"\$"))
    with st.chat_message("assistant"), st.spinner("Buscando en el sitio de Bancolombia…"):
        try:
            respuesta = _cliente().chat(pregunta, st.session_state.conversation_id)
        except ApiClientError as exc:
            st.session_state.error = (
                f"No se pudo responder «{shorten(pregunta)}». {friendly_error(exc)}"
            )
            if exc.status == 404:  # la conversación ya no existe: la próxima abre otra
                st.session_state.conversation_id = None
            return
    st.session_state.conversation_id = respuesta.conversation_id
    st.session_state.messages.append({"role": "user", "content": pregunta})
    st.session_state.messages.append(_mensaje_de_respuesta(respuesta))
    st.session_state.error = None


def main() -> None:
    """Página principal del chat."""
    st.set_page_config(page_title="Asistente de información pública", page_icon="💬")
    _iniciar_estado()
    detalle = _barra_lateral()

    st.title("Asistente de información pública")
    st.caption("Responde con el contenido público de www.bancolombia.com y cita sus fuentes.")
    st.info(AVISO, icon="⚠️")

    for i, m in enumerate(st.session_state.messages):
        _dibujar_mensaje(i, m, detalle)

    if pregunta := st.chat_input("Escribe tu pregunta…"):
        _preguntar(pregunta)
        st.rerun()

    if st.session_state.error:
        st.error(st.session_state.error)


main()
