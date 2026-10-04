"""Interfaz de línea de comandos del proyecto.

Uso: `python -m rag_bbva.cli <comando>` o `rag-bbva <comando>`.
Comandos disponibles: `version`, `scrape` (M2), `clean` (M3), `chunk` (M4), `ingest`
(M5), `search` (M6), `llm-check` (M7), `history` (M8), `serve` (M9, la API), `ui` y
`chat` (M10), `metrics` (M11) y `bootstrap` (M12, arranque con Docker).
"""

import json
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
import typer

from rag_bbva import __version__
from rag_bbva.config import get_settings
from rag_bbva.exceptions import (
    ConfigurationError,
    HistoryError,
    IndexingError,
    LLMError,
    ProcessingError,
    RetrievalError,
    ScrapingError,
)
from rag_bbva.indexing.factory import ComponentFactory
from rag_bbva.indexing.ingest import Ingestor, IngestReport
from rag_bbva.indexing.pipeline import ChunkReport, chunk_documents, read_documents, write_chunks
from rag_bbva.logging_conf import configure_logging
from rag_bbva.memory.models import Conversation, Message
from rag_bbva.processing.pipeline import CleaningPipeline, CleanReport, write_clean_output
from rag_bbva.retrieval.models import RetrievalResult
from rag_bbva.scraping.base import MOTIVO_INTERRUMPIDO, CrawlReport
from rag_bbva.scraping.crawler import SitemapBfsCrawler
from rag_bbva.scraping.fetcher import PoliteFetcher
from rag_bbva.scraping.storage import RawStorage, text_fingerprint

# Código de salida cuando el crawl se aborta por posible bloqueo del sitio.
EXIT_ABORTADO = 2
# Código de salida cuando el crawl se interrumpe (Ctrl+C o SIGTERM), como 128 + SIGINT.
EXIT_INTERRUMPIDO = 130

app = typer.Typer(
    name="rag-bbva",
    help="Asistente RAG sobre el sitio público de Bancolombia.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Inicializa el logging antes de ejecutar cualquier comando."""
    configure_logging()


@app.command()
def version() -> None:
    """Muestra la versión instalada del paquete."""
    typer.echo(f"rag-bbva {__version__}")


def _resumen_crawl(reporte: CrawlReport) -> str:
    """Resumen legible de una ejecución del crawler."""
    lineas = [
        f"Duración: {reporte.duration_seconds} s · peticiones HTTP: {reporte.requests_made}",
        f"Semillas: {reporte.seeds} · procesadas: {reporte.processed} · "
        f"enlaces encolados: {reporte.links_enqueued}",
        f"Resultados: {reporte.outcomes}",
        f"Códigos HTTP: {reporte.status_codes}",
        f"Omitidas: {reporte.skipped}",
        f"Entradas en el manifest: {reporte.manifest_entries}",
    ]
    if reporte.aborted:
        lineas.append(f"ABORTADO: {reporte.abort_reason}")
    return "\n".join(lineas)


@contextmanager
def _sigterm_como_interrupcion() -> Iterator[None]:
    """Convierte SIGTERM en `KeyboardInterrupt` mientras dura el bloque, para que un
    `kill` cierre el crawl igual que Ctrl+C (manifest y reporte con lo avanzado)."""

    def _manejador(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    anterior = signal.signal(signal.SIGTERM, _manejador)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, anterior)


@app.command()
def scrape(
    max_pages: Annotated[
        int | None,
        typer.Option(min=1, help="Máximo de páginas a procesar (default: CRAWL_MAX_PAGES)."),
    ] = None,
) -> None:
    """Descarga el sitio (sitemaps + BFS) a RAW_DATA_DIR respetando robots.txt."""
    settings = get_settings()
    base_url = str(settings.target_base_url)
    ua = settings.crawl_user_agent
    storage = RawStorage(settings.raw_data_dir, fingerprint=text_fingerprint)

    with httpx.Client(headers={"User-Agent": ua}, timeout=settings.crawl_timeout_seconds) as client:
        fetcher = PoliteFetcher(
            client,
            user_agent=ua,
            allowed_host=urlsplit(base_url).hostname or "",
            delay_seconds=settings.crawl_delay_seconds,
            max_retries=settings.crawl_max_retries,
            backoff_seconds=settings.crawl_backoff_seconds,
        )
        crawler = SitemapBfsCrawler(
            fetcher,
            storage,
            base_url=base_url,
            max_pages=max_pages or settings.crawl_max_pages,
            max_depth=settings.crawl_max_depth,
            block_threshold=settings.crawl_block_threshold,
            exclude_path_prefixes=settings.crawl_exclude_path_prefixes,
        )
        try:
            with _sigterm_como_interrupcion():
                reporte = crawler.crawl()
        except ScrapingError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    (storage.root / "crawl_report.json").write_text(
        json.dumps(reporte.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    typer.echo(_resumen_crawl(reporte))
    typer.echo(f"Datos crudos: {storage.root} (manifest.jsonl, pages/, crawl_report.json)")
    if reporte.abort_reason == MOTIVO_INTERRUMPIDO:
        raise typer.Exit(code=EXIT_INTERRUMPIDO)
    if reporte.aborted:
        raise typer.Exit(code=EXIT_ABORTADO)


def _resumen_limpieza(reporte: CleanReport) -> str:
    """Resumen legible de una ejecución de la limpieza."""
    lineas = [
        f"Páginas leídas: {reporte.processed} · conservadas: {reporte.kept}",
        f"Descartadas: {reporte.discarded}",
        f"No leídas del manifest: {reporte.manifest_skipped}",
        f"Caracteres: {reporte.n_chars.model_dump() if reporte.n_chars else '-'}",
        f"Por sección: {reporte.by_section}",
        f"Por plantilla: {reporte.by_template}",
        f"Extracción: {reporte.by_extraction}",
        f"Idioma: {reporte.by_lang} · distinto de <html lang>: {reporte.lang_mismatch} "
        f"{reporte.lang_mismatch_pairs} · sin señal (lang de <html lang>): "
        f"{reporte.lang_fallback_by_template}",
        f"Fugas de boilerplate: {reporte.leaks.total} {reporte.leaks.by_pattern}",
        f"Tablas: {reporte.tables.tables} en {reporte.tables.documents_with_tables} documentos "
        f"· filas: {reporte.tables.rows} · filas desalineadas: {reporte.tables.misaligned_rows} "
        f"· filas mal formadas: {reporte.tables.malformed_rows}",
    ]
    return "\n".join(lineas)


@app.command()
def clean() -> None:
    """Limpia el HTML de RAW_DATA_DIR y escribe documentos en CLEAN_DATA_DIR."""
    settings = get_settings()
    pipeline = CleaningPipeline.default(
        min_chars=settings.clean_min_chars,
        min_extraction_coverage=settings.clean_min_extraction_coverage,
    )
    try:
        resultado = pipeline.process_directory(settings.raw_data_dir)
        documentos, reporte = write_clean_output(resultado, settings.clean_data_dir)
    except ProcessingError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_resumen_limpieza(resultado.report))
    typer.echo(f"Datos limpios: {documentos} · reporte: {reporte}")


def _resumen_chunks(reporte: ChunkReport) -> str:
    """Resumen legible del chunking."""
    lineas = [
        f"Estrategia: {reporte.chunker} (tamaño {reporte.chunk_size}, "
        f"solapamiento {reporte.chunk_overlap})",
        f"Documentos: {reporte.documents} · chunks: {reporte.total}",
        f"Caracteres: {reporte.n_chars.model_dump() if reporte.n_chars else '-'}",
        f"Chunks por documento: "
        f"{reporte.chunks_per_document.model_dump() if reporte.chunks_per_document else '-'}",
        f"Por sección: {reporte.by_section}",
        f"Muy cortos (< {reporte.short_threshold} caracteres): {reporte.short_chunks}",
        f"Tokens: {reporte.tokens.model_dump() if reporte.tokens else '-'} · "
        f"máximo del modelo: {reporte.max_tokens} · lo superan: {reporte.over_max_tokens}",
    ]
    if reporte.over_max_tokens:
        lineas.append("ADVERTENCIA: hay chunks que el modelo truncaría; reduzca CHUNK_SIZE.")
    return "\n".join(lineas)


@app.command()
def chunk(
    strategy: Annotated[
        str | None,
        typer.Option(help="heading_aware | fixed_size (default: CHUNKING_STRATEGY)."),
    ] = None,
) -> None:
    """Trocea los documentos de CLEAN_DATA_DIR en chunks (CHUNKS_DATA_DIR)."""
    settings = get_settings()
    fabrica = ComponentFactory(settings)
    try:
        chunker = fabrica.create_chunker(strategy)
        documentos = read_documents(settings.clean_data_dir)
        # El embedder aporta el tokenizer real para verificar el máximo de tokens.
        resultado = chunk_documents(
            documentos, chunker, fabrica.create_embedder(), settings.chunk_min_chars
        )
        ruta_chunks, ruta_reporte = write_chunks(resultado, settings.chunks_data_dir)
    except (IndexingError, ConfigurationError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_resumen_chunks(resultado.report))
    typer.echo(f"Chunks: {ruta_chunks} · reporte: {ruta_reporte}")


def _resumen_ingesta(reporte: IngestReport) -> str:
    """Resumen legible de una ingesta."""
    t = reporte.times
    return "\n".join(
        [
            f"Colección: {reporte.collection} · estrategia: {reporte.chunker}"
            + (" · reconstruida (--recreate)" if reporte.recreated else ""),
            f"Documentos: {reporte.documents} · chunks: {reporte.chunks}",
            f"Nuevos: {reporte.new} · actualizados: {reporte.updated} · sin cambios: "
            f"{reporte.unchanged} · eliminados: {reporte.deleted}",
            f"Embebidos: {reporte.embedded} · desde caché: {reporte.from_cache}",
            f"Puntos en la colección: {reporte.points}",
            f"Tiempos (s): chunking {t.chunking} · plan {t.sync_plan} · embeddings "
            f"{t.embedding} · upsert {t.upsert} · borrado {t.delete} · total {t.total}",
        ]
    )


@app.command()
def ingest(
    recreate: Annotated[
        bool, typer.Option("--recreate", help="Borra y reconstruye la colección desde cero.")
    ] = False,
) -> None:
    """Indexa CLEAN_DATA_DIR en Qdrant: chunks → embeddings (con caché) → upsert."""
    settings = get_settings()
    fabrica = ComponentFactory(settings)
    try:
        documentos = read_documents(settings.clean_data_dir)
        ingestor = Ingestor(
            chunker=fabrica.create_chunker(),
            embedder=fabrica.create_embedder(),
            store=fabrica.create_vector_store(),
            cache=fabrica.create_embedding_cache(),
            collection=settings.qdrant_collection,
        )
        reporte = ingestor.run(documentos, recreate=recreate)
    except (IndexingError, ConfigurationError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_resumen_ingesta(reporte))


def _redondeo(valor: float | None) -> float | None:
    return None if valor is None else round(valor, 3)


def _formato_busqueda(resultado: RetrievalResult) -> str:
    """Resultados de `search` legibles para la demo y la depuración."""
    lineas = [
        f"Pregunta: {resultado.query}"
        + (f" · sección: {resultado.section}" if resultado.section else ""),
        f"Reranker: {resultado.reranker} · candidatos (top-k): {resultado.top_k} · "
        f"retrieval {resultado.retrieval_ms} ms · rerank {resultado.rerank_ms} ms",
    ]
    for i, c in enumerate(resultado.results, 1):
        rerank = f"{c.rerank_score:7.3f}" if c.rerank_score is not None else "      -"
        lineas.append(
            f"{i}. rerank {rerank} · coseno {c.cosine_score:.3f} · venía del #{c.retrieval_rank}"
        )
        lineas.append(f"   {c.url}")
        lineas.append(f"   {c.heading_path}")
    if resultado.min_score is None:
        lineas.append("Umbral: no se aplica sin reranker.")
    else:
        if not resultado.no_answer:
            veredicto = "lo supera: hay contexto para responder"
        elif resultado.gray_zone:
            veredicto = (
                f"NO lo supera, pero sí el umbral duro ({resultado.hard_min_score}): "
                "zona gris, el LLM decide si el contexto alcanza"
            )
        else:
            veredicto = "NO lo supera: sin información suficiente (no se llama al LLM)"
        lineas.append(
            f"Umbral RERANK_MIN_SCORE={resultado.min_score}: "
            f"el #1 ({_redondeo(resultado.top_score)}) {veredicto}."
        )
    return "\n".join(lineas)


@app.command()
def search(
    pregunta: Annotated[str, typer.Argument(help="Pregunta en lenguaje natural.")],
    no_rerank: Annotated[
        bool, typer.Option("--no-rerank", help="Solo similitud coseno, sin reranker.")
    ] = False,
    section: Annotated[
        str | None, typer.Option("--section", help="Filtra por sección (p. ej. personas).")
    ] = None,
    top_n: Annotated[
        int | None,
        typer.Option("--top-n", min=1, help="Resultados finales (default: RERANK_TOP_N)."),
    ] = None,
) -> None:
    """Busca en Qdrant con reranking y muestra url, ruta de títulos, scores y el umbral."""
    fabrica = ComponentFactory(get_settings())
    try:
        retriever = fabrica.create_retriever(rerank=False if no_rerank else None, top_n=top_n)
        retriever.warm_up()
        resultado = retriever.retrieve(pregunta, section=section)
    except (IndexingError, RetrievalError, ConfigurationError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_formato_busqueda(resultado))


# Código de salida de `llm-check` cuando la API responde pero LLM_MODEL no está disponible.
EXIT_MODELO_NO_DISPONIBLE = 3


@app.command("llm-check")
def llm_check() -> None:
    """Lista los modelos del proveedor (GET /models) y confirma que LLM_MODEL existe, sin
    gastar tokens."""
    settings = get_settings()
    try:
        modelos = ComponentFactory(settings).create_llm().list_models()
    except (ConfigurationError, LLMError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Modelos disponibles ({len(modelos)}):")
    for modelo in modelos:
        marca = "  <- LLM_MODEL" if modelo == settings.llm_model else ""
        typer.echo(f"  - {modelo}{marca}")
    if settings.llm_model in modelos:
        typer.echo(f"OK: LLM_MODEL={settings.llm_model} está disponible para esta clave.")
        return
    typer.echo(
        f"LLM_MODEL={settings.llm_model} NO está en la lista. Elija uno de los anteriores "
        "y configúrelo en .env (LLM_MODEL=...).",
        err=True,
    )
    raise typer.Exit(code=EXIT_MODELO_NO_DISPONIBLE)


def _formato_conversaciones(conversaciones: list[Conversation]) -> str:
    if not conversaciones:
        return "No hay conversaciones guardadas."
    lineas = [f"Conversaciones ({len(conversaciones)}, más recientes primero):"]
    for c in conversaciones:
        cuando = c.updated_at.isoformat(timespec="seconds")
        lineas.append(f"  {c.id}  {cuando}  {c.title or '(sin título)'}")
    return "\n".join(lineas)


def _formato_mensajes(conversacion: Conversation, mensajes: list[Message]) -> str:
    lineas = [f"Conversación {conversacion.id} · {conversacion.title or '(sin título)'}"]
    for m in mensajes:
        extra = []
        if m.sources:
            extra.append(f"fuentes={len(m.sources)}")
        if m.metrics.total_ms is not None:
            extra.append(f"total_ms={m.metrics.total_ms:.0f}")
        if m.feedback:
            extra.append(f"feedback={m.feedback}")
        detalle = f"  ({', '.join(extra)})" if extra else ""
        cuando = m.created_at.isoformat(timespec="seconds")
        lineas.append(f"[{m.id}] {cuando} {m.role}: {m.content}{detalle}")
    return "\n".join(lineas)


@app.command()
def history(
    conversation_id: Annotated[
        str | None, typer.Argument(help="ID de la conversación; sin él, lista conversaciones.")
    ] = None,
    last: Annotated[
        int | None,
        typer.Option("--last", min=0, help="Solo los últimos N mensajes (como get_last_n)."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Conversaciones a listar.")] = 20,
) -> None:
    """Muestra el historial guardado en HISTORY_DB_PATH (solo lectura)."""
    try:
        repo = ComponentFactory(get_settings()).create_conversation_repository()
        if conversation_id is None:
            typer.echo(_formato_conversaciones(repo.list_conversations(limit=limit)))
            return
        conversacion = repo.require_conversation(conversation_id)
        mensajes = (
            repo.get_messages(conversation_id)
            if last is None
            else repo.get_last_n(conversation_id, last)
        )
    except HistoryError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_formato_mensajes(conversacion, mensajes))


@app.command()
def serve(
    host: Annotated[
        str | None, typer.Option("--host", help="Dirección (default: API_HOST).")
    ] = None,
    port: Annotated[
        int | None, typer.Option("--port", min=1, max=65535, help="Puerto (default: API_PORT).")
    ] = None,
) -> None:
    """Levanta la API REST (FastAPI + uvicorn). Documentación interactiva en /docs."""
    import uvicorn

    from rag_bbva.api.app import create_app

    settings = get_settings()
    direccion, puerto = host or settings.api_host, port or settings.api_port
    typer.echo(
        f"API en http://{direccion}:{puerto} · documentación: http://{direccion}:{puerto}/docs"
    )
    # log_config=None: uvicorn usa el logging JSON del proyecto (configure_logging).
    uvicorn.run(create_app(settings), host=direccion, port=puerto, log_config=None)


@app.command()
def ui(
    host: Annotated[
        str | None, typer.Option("--host", help="Dirección (default: UI_HOST).")
    ] = None,
    port: Annotated[
        int | None, typer.Option("--port", min=1, max=65535, help="Puerto (default: UI_PORT).")
    ] = None,
    api_url: Annotated[
        str | None, typer.Option("--api-url", help="URL de la API (default: API_BASE_URL).")
    ] = None,
) -> None:
    """Levanta la interfaz web (Streamlit). Necesita la API corriendo (`serve`)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import rag_bbva.ui

    settings = get_settings()
    direccion, puerto = host or settings.ui_host, port or settings.ui_port
    api = api_url or settings.api_base_url
    app_ui = Path(rag_bbva.ui.__file__).parent / "streamlit_app.py"  # navegación (M14)
    typer.echo(f"Interfaz en http://{direccion}:{puerto} · API: {api}")
    comando = [
        sys.executable, "-m", "streamlit", "run", str(app_ui),
        "--server.address", direccion, "--server.port", str(puerto),
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
    ]  # fmt: skip
    resultado = subprocess.run(comando, env={**os.environ, "API_BASE_URL": api}, check=False)
    raise typer.Exit(code=resultado.returncode)


@app.command()
def bootstrap(
    force: Annotated[
        bool, typer.Option("--force", help="Indexa aunque la colección ya tenga puntos.")
    ] = False,
    sin_modelos: Annotated[
        bool,
        typer.Option("--sin-modelos", help="No carga (ni descarga) el embedder y el reranker."),
    ] = False,
) -> None:
    """Arranque para Docker: copia el snapshot si faltan datos, indexa si la colección está
    vacía y deja los modelos descargados. No scrapea el sitio."""
    from rag_bbva.indexing.bootstrap import Bootstrapper

    settings = get_settings()
    try:
        r = Bootstrapper(settings, ComponentFactory(settings)).run(
            force=force, warm_up=not sin_modelos
        )
    except (IndexingError, ConfigurationError, RetrievalError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    docs = "copiados del snapshot" if r.documents_from_snapshot else "ya existían"
    cache = "copiada del snapshot" if r.embeddings_cache_from_snapshot else "ya existía o no hay"
    typer.echo(f"Documentos limpios: {docs} · caché de embeddings: {cache}")
    if r.indexed and r.ingest:
        typer.echo(f"Colección vacía ({r.points_before} puntos): indexada.")
        typer.echo(_resumen_ingesta(r.ingest))
    else:
        typer.echo(f"Colección con {r.points_before} puntos: no se reindexa (use --force).")
    typer.echo("Modelos cargados." if r.models_warmed else "Modelos sin cargar (--sin-modelos).")


AVISO_DEMO = (
    "*** DATOS DE DEMOSTRACIÓN: conversaciones generadas por scripts/seed_conversations.py "
    "(los votos 👍/👎 los aplicó el script). No son uso real. ***"
)


def _fmt(valor: float | None, sufijo: str = "") -> str:
    return "—" if valor is None else f"{valor:,.1f}{sufijo}"


def _formato_metricas(r: Any) -> str:
    o, q, c, m, k, i = r.operational, r.quality, r.content, r.memory, r.cost, r.impact
    lineas = []
    if r.meta.demo:
        lineas.append(AVISO_DEMO)
    desde = r.meta.since.isoformat() if r.meta.since else "todo el historial"
    lineas += [
        f"Fuente: {r.meta.source} · desde: {desde} · zona horaria: {r.meta.timezone}",
        "",
        "== Operativas",
        f"Conversaciones: {o.conversations} · mensajes: {o.messages} · turnos: {o.turns}",
        f"Turnos por conversación: media {o.turns_per_conversation.mean} · "
        f"mediana {o.turns_per_conversation.median}",
        f"Turnos por día: {o.turns_by_day}",
        f"Turnos por hora: {o.turns_by_hour}",
        "Latencia (ms, rango más cercano):",
    ]
    for etapa, p in o.latency_ms.items():
        lineas.append(f"  {etapa:9s} n={p.n:3d} · p50 {_fmt(p.p50)} · p95 {_fmt(p.p95)}")
    lineas += [
        "",
        "== Calidad",
        f"Sin información: {q.no_answer.count} ({q.no_answer.pct} %) = "
        f"corte duro (score < {r.meta.rerank_hard_min_score}, sin LLM) {q.hard_cut.count} "
        f"({q.hard_cut.pct} %) + abstención del LLM {q.llm_abstention.count} "
        f"({q.llm_abstention.pct} %: {q.llm_abstention_gray_zone} en zona gris, "
        f"{q.llm_abstention_outside_gray_zone} fuera)",
        f"Turnos en zona gris: {q.gray_zone_turns.count} ({q.gray_zone_turns.pct} %)",
        f"Con al menos una fuente: {q.with_sources.count} ({q.with_sources.pct} %)",
        f"Score medio del reranker (#1): {_fmt(q.reranker_top_score_mean)}",
        f"Valoraciones: 👍 {q.feedback.up} · 👎 {q.feedback.down} · tasa 👍 "
        f"{q.feedback.up_rate_pct} % de las votadas · cobertura {q.feedback.coverage_pct} % "
        "de los turnos",
        "",
        "== Contenido",
        "URLs más citadas:",
        *[f"  {x.count:3d}  {x.name}" for x in c.top_urls],
        "Secciones más citadas: " + ", ".join(f"{x.name} ({x.count})" for x in c.top_sections),
        f"Preguntas frecuentes (agrupadas por {c.grouping}):",
        *[f"  {g.count:3d}  {g.question}" for g in c.frequent_questions],
        "Brechas de contenido (preguntas sin respuesta = oportunidades de contenido):",
        *[f"  {g.count:3d}  {g.question}" for g in c.content_gaps],
        "",
        "== Memoria",
        f"Conversaciones de más de un turno: {m.multi_turn_conversations.count} "
        f"({m.multi_turn_conversations.pct} %)",
        f"Turnos con pregunta reformulada: {m.rewritten_turns.count} ({m.rewritten_turns.pct} % "
        f"de {m.turns_with_rewrite_data} turnos con el dato)",
        "",
        "== Costo (estimación)",
        f"Tokens: {k.prompt_tokens:,} entrada + {k.completion_tokens:,} salida = "
        f"{k.total_tokens:,} · {k.tokens_per_turn} por turno",
        f"Costo estimado: US$ {k.estimated_cost_usd:.6f} · US$ {k.estimated_cost_per_turn_usd:.6f}"
        f" por turno (precios US$ {k.price_input_per_mtok}/{k.price_output_per_mtok} por millón)",
        f"  {k.note}",
        "",
        "== Impacto (estimación)",
        f"Consultas resueltas: {i.resolved_turns} · tasa de resolución {i.resolution_rate_pct} %",
        f"Horas de búsqueda manual ahorradas: {i.hours_saved} "
        f"(= {i.resolved_turns} consultas · {i.manual_search_minutes} min / 60)",
        "Costo por consulta resuelta: "
        + ("—" if i.cost_per_resolved_usd is None else f"US$ {i.cost_per_resolved_usd:.6f}"),
        f"  Supuesto: {i.assumption}",
    ]
    if r.meta.demo:
        lineas.append(AVISO_DEMO)
    return "\n".join(lineas)


def _fecha(texto: str | None, zona: str) -> "datetime | None":
    """`AAAA-MM-DD` → medianoche de ese día en la zona horaria de la analítica."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if texto is None:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d").replace(tzinfo=ZoneInfo(zona))
    except ValueError as exc:
        raise typer.BadParameter("Use el formato AAAA-MM-DD", param_hint="--since") from exc


@app.command()
def metrics(
    since: Annotated[
        str | None,
        typer.Option("--since", help="Desde esta fecha (AAAA-MM-DD, zona de la analítica)."),
    ] = None,
    db: Annotated[
        Path | None, typer.Option("--db", help="Base del historial (default: HISTORY_DB_PATH).")
    ] = None,
    export: Annotated[
        str | None, typer.Option("--export", help="Exporta: csv (tablas) o json (resumen).")
    ] = None,
    out: Annotated[Path, typer.Option("--out", help="Carpeta del export.")] = Path(
        "data/analytics"
    ),
    sin_embeddings: Annotated[
        bool,
        typer.Option("--sin-embeddings", help="Agrupa preguntas por texto (no carga el modelo)."),
    ] = False,
) -> None:
    """Analítica del historial: operativas, calidad, contenido, memoria, costo e impacto."""
    settings = get_settings()
    if export not in (None, "csv", "json"):
        raise typer.BadParameter("Use csv o json", param_hint="--export")
    desde = _fecha(since, settings.analytics_timezone)
    ruta = db or settings.history_db_path
    if not ruta.is_file():
        typer.echo(f"Error: no existe la base del historial {ruta}", err=True)
        raise typer.Exit(code=1)
    try:
        servicio = ComponentFactory(settings).create_analytics_service(
            db_path=ruta, with_embedder=not sin_embeddings
        )
        resumen = servicio.summary(desde)
        typer.echo(_formato_metricas(resumen))
        if export:
            rutas = servicio.export(out, export, desde)  # type: ignore[arg-type]
            typer.echo(f"Export ({export}): " + ", ".join(str(r) for r in rutas))
    except HistoryError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


_SALIR = {"salir", "exit", "quit", ":q"}


@app.command()
def chat(
    conversation_id: Annotated[
        str | None,
        typer.Option("--conversation-id", help="Continúa una conversación existente."),
    ] = None,
) -> None:
    """Chat de respaldo en la terminal, directo sobre RAGService (sin API ni UI).
    Escriba `salir` o deje la línea vacía para terminar."""
    from rag_bbva.exceptions import RagBbvaError
    from rag_bbva.ui.render import AVISO

    fabrica = ComponentFactory(get_settings())
    try:
        servicio = fabrica.create_rag_service()
        if conversation_id:
            conversacion, mensajes = servicio.get_conversation(conversation_id)
            typer.echo(
                f"Continuando «{conversacion.title or '(sin título)'}» "
                f"({len(mensajes)} mensajes previos)."
            )
        typer.echo("Cargando modelos…")
        servicio.warm_up()
    except (ConfigurationError, HistoryError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Asistente de información pública. {AVISO} Escriba `salir` para terminar.")
    while True:
        try:
            pregunta = typer.prompt("Tú", default="", show_default=False).strip()
        except (EOFError, typer.Abort):
            break
        if not pregunta or pregunta.lower() in _SALIR:
            break
        try:
            r = servicio.ask(conversation_id, pregunta)
        except RagBbvaError as exc:
            typer.echo(f"No se pudo responder: {exc.message}", err=True)
            continue
        conversation_id = r.conversation_id
        if r.no_answer:
            typer.echo("[Sin información suficiente]")
        typer.echo(f"Asistente: {r.answer}")
        for s in r.sources:
            typer.echo(f"  [{s.n}] {s.title or s.url} — {s.url}")
    if conversation_id:
        typer.echo(f"Conversación: {conversation_id}")


if __name__ == "__main__":
    app()
