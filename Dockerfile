FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TEMP_PROCESSING_DIR=/tmp/rag-processing

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /nonexistent --shell /usr/sbin/nologin app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app api ./api
COPY --chown=app:app core ./core
COPY --chown=app:app db ./db
COPY --chown=app:app services ./services
COPY --chown=app:app tools/__init__.py tools/setup_pinecone.py tools/ingest.py tools/query_rag.py tools/cleanup_expired.py tools/reconcile_persistence.py ./tools/
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app docker ./docker

RUN mkdir -p "$TEMP_PROCESSING_DIR" \
    && chmod 0555 /app/docker/entrypoint.sh \
    && chown -R app:app /app "$TEMP_PROCESSING_DIR"

USER app

EXPOSE 10000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
