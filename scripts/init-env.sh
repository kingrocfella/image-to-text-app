#!/bin/sh
# Create the one canonical .env with safe local-development defaults.
# Secrets are generated; SMTP and the model provider keys must be filled in by
# hand. Refuses to touch an existing file.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_path="$repo_dir/.env"

if [ -e "$env_path" ]; then
  echo "init-env: .env already exists; leaving it untouched" >&2
  exit 1
fi

secret_key=$(openssl rand -hex 32)
postgres_password=$(openssl rand -hex 24)
umask 077
temporary_path=$(mktemp "$repo_dir/.init-env.XXXXXX")
trap 'rm -f "$temporary_path"' EXIT HUP INT TERM

sed \
  -e "s|__SECRET_KEY__|$secret_key|g" \
  -e "s|__POSTGRES_PASSWORD__|$postgres_password|g" \
  > "$temporary_path" <<'EOF'
# ScanGenAI — the ONLY environment file in this workspace (AGENTS.md §1).
#
# Gitignored, mode 0600. app/config.py reads every setting from here and
# refuses to start on a malformed value. Real process variables win over this
# file, so Compose and deployments override values without editing it.
# `make check-env` fails if a key the code reads is missing or duplicated, or
# if a key is here that nothing reads.

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
# dev or production — nothing else. Production enforces an https APP_URL, SMTP
# and file logging, turns on HSTS and hides the interactive docs.
ENVIRONMENT=dev
# Public origin used in emailed links. No path. https in production.
APP_URL=http://127.0.0.1:8000
# Comma-separated browser origins. Empty (the mobile app needs none) or an
# explicit list; never *.
CORS_ALLOWED_ORIGINS=
# Oldest app version the API serves; older builds get HTTP 426. Builds from
# before /v1 send no version, so raising this above 0.0.0 retires them.
MINIMUM_APP_VERSION=0.0.0
MAX_REQUEST_BODY_BYTES=26214400
REQUEST_TIMEOUT_SECONDS=30
# true behind the VPS reverse proxy: rate limits then key on the last
# X-Forwarded-For entry (the one the proxy added). false for direct runs.
TRUST_PROXY_HEADERS=false
# Host port Compose publishes the API on, 127.0.0.1 only. Shared VPS: allocated,
# not defaulted — check the other apps before changing any *_HOST_PORT.
API_HOST_PORT=8000

# ---------------------------------------------------------------------------
# Logging — info.log + errors.log in LOG_DIR. Compose uses /app/logs.
# ---------------------------------------------------------------------------
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_DIR=logs
LOG_MAX_SIZE_MB=10
LOG_BACKUP_COUNT=5

# ---------------------------------------------------------------------------
# Datastores
# ---------------------------------------------------------------------------
POSTGRES_USER=scangenai
POSTGRES_PASSWORD=__POSTGRES_PASSWORD__
POSTGRES_DB=scangenai
# Compose service name. A host-run process uses localhost + POSTGRES_HOST_PORT.
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_HOST_PORT=5433
# Optional real-PostgreSQL database for the test suite; empty runs it on SQLite
# and skips the PostgreSQL-only tests. Never point it at the application
# database: the tests drop and rebuild the schema.
TEST_DATABASE_URL=
# Redis is the Dramatiq job queue and result store (not the rate limiter).
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_HOST_PORT=6382
QDRANT_URL=http://qdrant:6333
QDRANT_HOST_PORT=6333
# Dramatiq worker threads.
WORKER_THREADS=8

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
SECRET_KEY=__SECRET_KEY__
# Set by `make rotate-secrets`: the key before the last rotation, still
# accepted for verification so rotating never signs anyone out. Empty is fine.
SECRET_KEY_PREVIOUS=
# Days between scheduled rotations (scripts/rotate-secrets-cron.sh). Keep it at
# least REFRESH_TOKEN_EXPIRE_DAYS long, or rotations will refuse to run.
SECRET_ROTATION_INTERVAL_DAYS=90
JWT_ISSUER=scangenai-api
JWT_AUDIENCE=scangenai-client
ACCESS_TOKEN_EXPIRE_HOURS=1
# How long a signed-in phone stays signed in without being opened.
REFRESH_TOKEN_EXPIRE_DAYS=30
VERIFICATION_TOKEN_EXPIRE_HOURS=24
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES=60
# How often expired blacklist entries, refresh sessions and old job records
# are deleted.
SECURITY_PURGE_INTERVAL_SECONDS=3600

# ---------------------------------------------------------------------------
# Outbound email (verification, password reset, operator alerts)
# ---------------------------------------------------------------------------
SMTP_SERVER=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_NAME=Leon Frontier ScanGenAI

# ---------------------------------------------------------------------------
# Upload bounds
# ---------------------------------------------------------------------------
IMAGE_MAX_BYTES=10485760
IMAGE_MAX_PIXELS=40000000
IMAGE_MAX_FRAMES=20
AUDIO_MAX_BYTES=20971520
PDF_MAX_BYTES=20971520
PDF_MAX_PAGES=100

# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
# PDF embeddings are deleted this many days after upload.
RAG_RETENTION_DAYS=30
# Job results and their ownership metadata live in Redis this long.
JOB_TYPE_TTL_DAYS=7

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
# The single shared Lost Vowels daemon on the word-games-ollama network.
OLLAMA_URL=http://ollama:11434
OLLAMA_MODEL=llama3.2:3b
OLLAMA_TEMPERATURE=0.7
OLLAMA_NUM_PREDICT=500
OLLAMA_TIMEOUT_SECONDS=300
# Dev-only sidecar (`--profile ollama`); never used on the shared VPS.
OLLAMA_KEEP_ALIVE=10m
OLLAMA_HOST_PORT=11438
# Required for every PDF question (embeddings), and for the "openai" model.
# Empty turns PDF questions off entirely.
OPENAI_API_KEY=
# Empty means that model is not offered to the app.
GEMINI_API_KEY=
DEEPSEEK_API_KEY=
ANTHROPIC_API_KEY=
# Which model each provider answers with: the least expensive current one that
# does the job (docs/models.md has the prices and the reasoning). Change a
# model here, never in code. GEMINI_MODEL is the newest model on Google's free
# tier; on a free-tier key Google may use the content to improve its products.
OPENAI_MODEL=gpt-6-luna
GEMINI_MODEL=gemini-3.8-flash
DEEPSEEK_MODEL=deepseek-flash
CLAUDE_MODEL=claude-haiku-4-5
# Used for new PDFs. Each stored PDF remembers the model that indexed it.
EMBEDDING_MODEL=text-embedding-3-small
# Chunks of the PDF sent with each question, and the cap on an answer's length.
# These two bound what one question can cost.
RAG_TOP_K=8
CLOUD_MODEL_MAX_OUTPUT_TOKENS=800
# Cloud models a free account may use, within its allowance. Everything else
# needs ScanGenAI Pro. openai is refused here: it is for subscribers only.
FREE_CLOUD_MODELS=gemini,deepseek

# ---------------------------------------------------------------------------
# Per-user monthly allowances (docs/billing.md). All of this work is billed to
# the operator. 0 turns that kind of work off for that plan.
# ---------------------------------------------------------------------------
# Free
QUOTA_IMAGE_MONTHLY=100
QUOTA_SOUND_MONTHLY=30
# Every PDF question, whichever model answers it (it always costs embeddings).
QUOTA_PDF_MONTHLY=50
# Answers from a cloud model, counted together.
QUOTA_CLOUD_MODEL_MONTHLY=10
# ScanGenAI Pro (fair-use caps, not selling points)
PRO_QUOTA_IMAGE_MONTHLY=1000
PRO_QUOTA_SOUND_MONTHLY=300
PRO_QUOTA_PDF_MONTHLY=500
PRO_QUOTA_CLOUD_MODEL_MONTHLY=300

# ---------------------------------------------------------------------------
# ScanGenAI Pro subscriptions (docs/billing.md)
# ---------------------------------------------------------------------------
# off: no purchases (the paywall says plans are coming). dev: fake receipts
# for local testing, refused in production. store: real App Store / Play
# verification; needs the values below.
BILLING_PROVIDER=dev
# Production also accepts Sandbox (App Review and TestFlight buy in Sandbox).
APPLE_ENVIRONMENT=Production
# Numeric Apple ID of the app in App Store Connect (App Information).
APPLE_APP_ID=0
APPLE_ROOT_CA_PATH=certs/apple-root-ca-g3.pem
# Play Console service account key with "View financial data", as one line.
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON=

# ---------------------------------------------------------------------------
# Social sign-in and native app identity
# ---------------------------------------------------------------------------
# Google Web OAuth client ID; the audience every Google ID token must carry.
# Must equal GOOGLE_WEB_CLIENT_ID in the app's src/constants. Empty disables
# Google sign-in.
GOOGLE_WEB_CLIENT_ID=
GOOGLE_OAUTH_TIMEOUT_S=5
ANDROID_PACKAGE_NAME=com.leonfrontier.scangenai
# Also the audience of a Sign in with Apple token.
IOS_BUNDLE_ID=com.leonfrontier.scangenai

# ---------------------------------------------------------------------------
# Operations: admin console, operator email, monitoring (docs/operations.md)
# ---------------------------------------------------------------------------
# The read-only console is off while the token is empty. To turn it on, set a
# 64-character token (openssl rand -hex 32) and an unguessable path
# (/ops-$(openssl rand -hex 8)); production refuses a short path.
ADMIN_DASHBOARD_USERNAME=admin
ADMIN_DASHBOARD_TOKEN=
ADMIN_DASHBOARD_PATH=/admin
ADMIN_AUTO_LOGOUT_MINUTES=15
# Failed sign-ins before the console locks, and for how long.
ADMIN_LOCK_THRESHOLD=5
ADMIN_LOCK_MINUTES=30
# Operator alerts (stale backups, failing or stuck jobs). Off by default so
# development and tests never send mail; uses the SMTP_* account above.
NOTIFY_EMAILS_ENABLED=false
NOTIFY_EMAIL_TO=
# Alert when the newest verified backup is older than this.
BACKUP_MAX_AGE_HOURS=48
OPS_CHECK_INTERVAL_MINUTES=60
# An unresolved alert is mailed again after this long.
OPS_ALERT_REPEAT_HOURS=24
EOF

chmod 600 "$temporary_path"
mv "$temporary_path" "$env_path"
trap - EXIT HUP INT TERM
echo "init-env: wrote .env (mode 0600)"
echo "init-env: fill in SMTP_* and OPENAI_API_KEY (PDF questions need it) before 'make up'"
