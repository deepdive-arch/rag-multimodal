#!/bin/sh
set -eu

echo "Validando configuração do backend..."
python - <<'PY'
from core.config import Settings

settings = Settings()
required = {
    "DATABASE_URL": settings.database_url,
    "GOOGLE_API_KEY": settings.google_api_key,
    "PINECONE_API_KEY": settings.pinecone_api_key,
    "R2_ACCESS_KEY_ID": settings.r2_access_key_id,
    "R2_SECRET_ACCESS_KEY": settings.r2_secret_access_key,
    "R2_BUCKET_NAME": settings.r2_bucket_name,
    "R2_ENDPOINT_URL": settings.r2_endpoint_url,
}
missing = [name for name, value in required.items() if not str(value).strip()]
if missing:
    raise SystemExit("Configuração incompleta; variáveis ausentes: " + ", ".join(missing))

print(
    "Configuração válida: "
    f"env={settings.app_env}, "
    f"database=configured, r2=configured, pinecone=configured, gemini=configured, "
    f"temp_dir={settings.temp_processing_dir}"
)
PY

echo "Aplicando migrations Alembic..."
if ! python -m alembic upgrade head; then
    echo "ERRO: as migrations do Postgres falharam; o Uvicorn não será iniciado." >&2
    exit 1
fi

echo "Iniciando FastAPI na porta ${PORT:-10000}..."
exec python -m uvicorn api.server:app \
    --host 0.0.0.0 \
    --port "${PORT:-10000}" \
    --workers 1 \
    --no-access-log
