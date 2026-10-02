# Imagen base del proyecto (M0). Se optimiza en M12: multi-stage, torch CPU-only, modelos.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app:app /app/data
USER app

CMD ["python", "-m", "rag_bbva.cli", "version"]
