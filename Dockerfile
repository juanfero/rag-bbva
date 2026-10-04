# Imagen del asistente (M12): una sola imagen para init (bootstrap), api y ui.
# Multi-stage: la etapa de build instala dependencias en un venv; la final solo copia
# el venv, el snapshot de datos y crea el usuario no root.

FROM python:3.11-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# torch solo CPU desde el índice de PyTorch: evita bajar CUDA (~GB) desde PyPI.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    # Todo lo que descarga Hugging Face va al volumen de modelos (/app/models).
    HF_HOME=/app/models/.hf

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
# Snapshot versionado de datos limpios y embeddings: el arranque no scrapea el sitio.
COPY snapshot ./snapshot

# Usuario no root. data/ y models/ existen con su dueño para que los volúmenes con
# nombre se creen con esos permisos.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data /app/models \
    && chown -R app:app /app
USER app

EXPOSE 8000 8501
CMD ["rag-bbva", "serve", "--host", "0.0.0.0", "--port", "8000"]
