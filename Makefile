# ScanGenAI API — developer commands.
# `make up` builds and runs the API, worker, PostgreSQL, Qdrant and Redis.
#
# NOTE: this app does NOT run its own Ollama daemon on the shared VPS. Lost
# Vowels runs the single daemon there and this API reaches it over the private
# `word-games-ollama` network, exactly as Letterbolt does. The local `ollama`
# service is behind an opt-in profile for development machines only:
#   docker compose --profile ollama up -d

.PHONY: up down restart logs worker-logs ps rebuild sh check-env init-env ensure-ollama-network \
	install-dev format format-check lint type-check test check clean help \
	backup backup-cron backup-cron-remove backup-log restore

COMPOSE = docker compose --env-file .env
LOG_TAIL ?= 200

help:
	@echo "Stack:   make up | down | restart | logs | worker-logs | ps | rebuild | sh"
	@echo "Env:     make check-env | init-env"
	@echo "Ollama:  make ensure-ollama-network   (shared Lost Vowels daemon)"
	@echo "Quality: make format | format-check | lint | type-check | test | check"
	@echo "         make install-dev | clean"
	@echo "Backups: make backup | restore | backup-cron | backup-cron-remove | backup-log"

# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------

## Create the private cross-project network the single shared Ollama daemon
## lives on. Idempotent; Lost Vowels and Letterbolt create the same network.
ensure-ollama-network:
	@docker network inspect word-games-ollama >/dev/null 2>&1 || \
		docker network create --driver bridge --internal word-games-ollama >/dev/null

## Start the API + worker + datastores in the background (builds if needed).
up: ensure-ollama-network
	chmod 600 .env
	$(MAKE) check-env
	$(COMPOSE) up -d --build

## Stop and remove the containers. Named volumes survive; `down -v` clears them.
down:
	$(COMPOSE) down

## Restart the API container.
restart:
	$(COMPOSE) restart web

## Follow API logs. Override history with LOG_TAIL=500 or LOG_TAIL=all.
logs:
	$(COMPOSE) logs --follow --tail=$(LOG_TAIL) web

## Follow the background worker's logs.
worker-logs:
	$(COMPOSE) logs --follow --tail=$(LOG_TAIL) worker

## Show container status.
ps:
	$(COMPOSE) ps

## Rebuild the API image from scratch (no cache).
rebuild:
	$(COMPOSE) build --no-cache web

## Open a shell in the API container.
sh:
	$(COMPOSE) exec web sh

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------

## .env is the only environment file allowed anywhere in this repo, it must
## be mode 0600, and it must carry exactly one entry for every key init-env
## emits — so a variable the code starts reading can never be silently absent.
check-env:
	@test -f .env || (echo "check-env: .env is missing; run 'make init-env'" >&2; exit 1)
	@extra=$$(find . -name '.env' -o -name '.env.*' 2>/dev/null \
		| grep -Ev '(^|/)(node_modules|\.git|\.venv|venv|\.next|\.claude)/' \
		| grep -v '^\./.env$$' || true); \
	if [ -n "$$extra" ]; then \
		echo "check-env: only .env is allowed; remove:" >&2; echo "$$extra" | sed 's/^/  /' >&2; exit 1; \
	fi
	@mode=$$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env); \
	if [ "$$mode" != "600" ]; then \
		echo "check-env: .env permissions are $$mode; expected 600" >&2; exit 1; \
	fi
	@bad=$$(grep -oE "^[[:space:]]+['\"][A-Z][A-Z0-9_]*=" Makefile | grep -oE "[A-Z][A-Z0-9_]*" | sort -u \
		| while read -r key; do \
			[ "$$(grep -c "^$$key=" .env)" -eq 1 ] || echo "  $$key"; \
		done); \
	if [ -n "$$bad" ]; then \
		echo "check-env: .env needs exactly one entry per init-env key; missing or duplicated:" >&2; \
		echo "$$bad" >&2; exit 1; \
	fi
	@echo "check-env: clean — .env is complete and mode 0600"

## Create the one canonical .env with safe local defaults (only if missing).
## SECRET_KEY / OPENAI_PASS / POSTGRES_PASSWORD are generated. SMTP and the
## cloud model keys must be filled in by hand — the code refuses placeholders.
init-env:
	@if [ -f .env ]; then \
		echo "init-env: .env already exists; leaving it untouched"; \
	else \
		printf '%s\n' \
			'ENVIRONMENT=dev' \
			'APP_HOST=0.0.0.0' \
			'APP_PORT=8000' \
			'APP_DEBUG=true' \
			'APP_URL=http://127.0.0.1:8000' \
			'API_HOST_PORT=8000' \
			"SECRET_KEY=$$(openssl rand -hex 32)" \
			'JWT_ISSUER=scangenai-api' \
			'JWT_AUDIENCE=scangenai-client' \
			'ACCESS_TOKEN_EXPIRE_HOURS=1' \
			'REFRESH_TOKEN_EXPIRE_DAYS=1' \
			'CORS_ALLOWED_ORIGINS=' \
			'MAX_REQUEST_BODY_BYTES=26214400' \
			'REQUEST_TIMEOUT_SECONDS=30' \
			'IMAGE_MAX_BYTES=10485760' \
			'IMAGE_MAX_PIXELS=40000000' \
			'IMAGE_MAX_FRAMES=20' \
			'AUDIO_MAX_BYTES=20971520' \
			'PDF_MAX_BYTES=20971520' \
			'PDF_MAX_PAGES=100' \
			'RAG_RETENTION_DAYS=30' \
			'JOB_TYPE_TTL_DAYS=7' \
			'POSTGRES_USER=scangenai' \
			"POSTGRES_PASSWORD=$$(openssl rand -hex 24)" \
			'POSTGRES_DB=scangenai' \
			'POSTGRES_HOST=postgres' \
			'POSTGRES_PORT=5432' \
			'POSTGRES_HOST_PORT=5433' \
			'REDIS_HOST=redis' \
			'REDIS_PORT=6379' \
			'REDIS_DB=0' \
			'REDIS_HOST_PORT=6382' \
			'QDRANT_URL=http://qdrant:6333' \
			'QDRANT_HOST_PORT=6333' \
			'OLLAMA_URL=http://ollama:11434' \
			'OLLAMA_MODEL=llama3.2:3b' \
			'OLLAMA_TEMPERATURE=0.7' \
			'OLLAMA_NUM_PREDICT=500' \
			'OLLAMA_KEEP_ALIVE=10m' \
			'OLLAMA_HOST_PORT=11438' \
			'WORKER_THREADS=8' \
			'SMTP_SERVER=change-me' \
			'SMTP_PORT=587' \
			'SMTP_USERNAME=change-me' \
			'SMTP_PASSWORD=change-me' \
			"OPENAI_PASS=$$(openssl rand -hex 16)" \
			'GEMINI_API_KEY=' \
			'DEEPSEEK_API_KEY=' \
			'LOG_LEVEL=INFO' \
			'LOG_DIR=/app/logs' > .env; \
		chmod 600 .env; \
		echo "init-env: wrote .env with safe local defaults"; \
		echo "init-env: fill in the SMTP settings and any cloud model keys before 'make up'"; \
	fi

# ---------------------------------------------------------------------------
# Quality / tests
# ---------------------------------------------------------------------------

install-dev:
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

format:
	@echo "Running isort..."
	isort app/ tests/
	@echo "Running black..."
	black app/ tests/

format-check:
	@echo "Checking isort..."
	isort --check-only app/ tests/
	@echo "Checking black..."
	black --check app/ tests/

lint:
	@echo "Running flake8..."
	flake8 app/

type-check:
	@echo "Running mypy..."
	mypy app/

test:
	@echo "Running pytest..."
	pytest

check: format-check lint type-check test
	@echo "All checks passed!"

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage build/ dist/

# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

## Create and validate a mode-0600 PostgreSQL dump (plus a Qdrant snapshot) under ./backups, then keep only
## the newest BACKUP_RETENTION copies (default 2). Pruning happens only after
## the new copy is written and verified.
backup:
	./scripts/backup-db.sh

## Install the daily backup cron entry for this user (idempotent). Runs at
## 01:40 host time; SCHEDULE='0 2 * * *' picks another. Every app on the
## shared VPS is staggered so they never contend in the same minute.
backup-cron:
	SCHEDULE="$(SCHEDULE)" ./scripts/install-backup-cron.sh

## Remove the daily backup cron entry. Existing copies are left alone.
backup-cron-remove:
	./scripts/install-backup-cron.sh --uninstall

## Show what the scheduled backups have been doing.
backup-log:
	@tail -n 40 backups/backup.log 2>/dev/null || echo "No scheduled backup has run yet."

## Restore BACKUP into the local Compose database. Requires CONFIRM=restore.
## Destructive: the restore replaces what is there now.
restore:
	@test "$(CONFIRM)" = "restore" || (echo "Refusing restore: pass CONFIRM=restore" >&2; exit 1)
	@test -n "$(BACKUP)" || (echo "Refusing restore: pass BACKUP=/absolute/path/file.dump" >&2; exit 1)
	./scripts/restore-db.sh "$(BACKUP)" --confirm
