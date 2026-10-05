# ScanGenAI API — developer commands.
# `make up` builds and runs the API, worker, PostgreSQL, Qdrant and Redis.
#
# NOTE: this app does NOT run its own Ollama daemon on the shared VPS. Lost
# Vowels runs the single daemon there and this API reaches it over the private
# `word-games-ollama` network, exactly as Letterbolt does. The local `ollama`
# service is behind an opt-in profile for development machines only:
#   docker compose --profile ollama up -d
#
# SQL migrations are applied automatically on API startup (see
# app/database/postgres.py:run_sql_migrations), so there is no `migrate` target.

.PHONY: up down restart logs worker-logs log-files error-files ps rebuild sh check-env init-env \
	ensure-ollama-network generate-routes check-routes install-dev format format-check lint \
	type-check quality audit test check clean help \
	backup backup-cron backup-cron-remove backup-log restore \
	rotate-secrets rotate-cron rotate-cron-remove rotate-cron-log unlock-admin \
	seed-reviewer-account

COMPOSE = docker compose --env-file .env
PYTHON ?= python
LOG_TAIL ?= 200
# Which service's saved log files log-files / error-files read: web or worker.
SERVICE ?= web
# tail has no "all": +1 means from the first line.
FILE_TAIL = $(if $(filter all,$(LOG_TAIL)),+1,$(LOG_TAIL))

help:
	@echo "Stack:   make up | down | restart | logs | worker-logs | ps | rebuild | sh"
	@echo "Logs:    make log-files | error-files [SERVICE=worker] [LOG_TAIL=500]   (saved; survive make up)"
	@echo "Env:     make check-env | init-env"
	@echo "Routes:  make generate-routes | check-routes   (mirror in ../image-text-react)"
	@echo "Ollama:  make ensure-ollama-network   (shared Lost Vowels daemon)"
	@echo "Quality: make quality | audit | test | check   (check = the gates to pass before a change is done)"
	@echo "         make format | format-check | lint | type-check   (full, pre-ratchet output)"
	@echo "         make install-dev | clean"
	@echo "Backups: make backup | restore | backup-cron | backup-cron-remove | backup-log"
	@echo "Secrets: make rotate-secrets [FORCE=1] | rotate-cron | rotate-cron-remove | rotate-cron-log"
	@echo "Admin:   make unlock-admin   (clear a console lockout)"
	@echo "Stores:  make seed-reviewer-account EMAIL=...   (create or reset the review sign-in)"

# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------

## Create the private cross-project network the single shared Ollama daemon
## lives on. Idempotent; Lost Vowels and Letterbolt create the same network.
ensure-ollama-network:
	@docker network inspect word-games-ollama >/dev/null 2>&1 || \
		docker network create --driver bridge --internal word-games-ollama >/dev/null

## Start the API + worker + datastores in the background (builds if needed).
## The API has a Docker health check: a wrong database password stops it at
## startup, so `make ps` shows it unhealthy rather than quietly half-working.
up: ensure-ollama-network
	chmod 600 .env
	$(MAKE) check-env
	$(COMPOSE) up -d --build --remove-orphans

## Stop and remove the containers. Named volumes survive; `down -v` clears them.
down:
	$(COMPOSE) down

## Restart the API container.
restart:
	$(COMPOSE) restart web

## Follow API logs (stdout). Override history with LOG_TAIL=500 or LOG_TAIL=all.
logs:
	$(COMPOSE) logs --follow --tail=$(LOG_TAIL) web

## Follow the background worker's logs (stdout).
worker-logs:
	$(COMPOSE) logs --follow --tail=$(LOG_TAIL) worker

## Follow the saved info.log of SERVICE (web by default; SERVICE=worker).
log-files:
	$(COMPOSE) exec $(SERVICE) tail -n $(FILE_TAIL) -F /app/logs/info.log

## Follow the saved errors.log of SERVICE (web by default; SERVICE=worker).
error-files:
	$(COMPOSE) exec $(SERVICE) tail -n $(FILE_TAIL) -F /app/logs/errors.log

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

## .env is the only environment file allowed anywhere in this workspace, mode
## 0600, with exactly one entry for every key the code reads and no key that
## nothing reads; and app/config.py is the only module that reads the
## environment (AGENTS.md §1).
check-env:
	@./scripts/check-env.sh

## Create the one canonical .env with safe local defaults (refuses to overwrite).
init-env:
	@./scripts/init-env.sh

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

## Regenerate ../image-text-react/src/api/routes.generated.ts from app/paths.py.
generate-routes:
	$(PYTHON) -m scripts.generate_routes

## Fail if the mobile route mirror no longer matches app/paths.py.
check-routes:
	$(PYTHON) -m scripts.generate_routes --check

# ---------------------------------------------------------------------------
# Quality / tests
# ---------------------------------------------------------------------------

## The API-layer tooling only. The OCR / speech / RAG stack in requirements.txt
## is several gigabytes and is not needed for `make check`: the tests mock it.
install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

format:
	@echo "Running isort..."
	$(PYTHON) -m isort app/ tests/ scripts/
	@echo "Running black..."
	$(PYTHON) -m black app/ tests/ scripts/

format-check:
	@echo "Checking isort..."
	$(PYTHON) -m isort --check-only app/ tests/ scripts/
	@echo "Checking black..."
	$(PYTHON) -m black --check app/ tests/ scripts/

lint:
	@echo "Running flake8..."
	$(PYTHON) -m flake8 app/

type-check:
	@echo "Running mypy..."
	$(PYTHON) -m mypy app/

test:
	@echo "Running pytest..."
	$(PYTHON) -m pytest

## flake8, mypy, black and isort against the recorded baseline: fails on new
## findings, and on fixed ones until the baseline is lowered.
quality:
	$(PYTHON) -m scripts.quality_ratchet

## Known vulnerabilities in everything the image installs. Zero, no exceptions:
## fix an advisory rather than excuse it.
audit:
	$(PYTHON) -m pip_audit -r requirements.txt --no-deps --disable-pip

## Everything a change must pass before it is done (AGENTS.md §10). Set
## TEST_DATABASE_URL to a throwaway database to include the PostgreSQL tests.
check: check-env quality check-routes test audit
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

# ---------------------------------------------------------------------------
# Secrets and admin
# ---------------------------------------------------------------------------

## Rotate POSTGRES_PASSWORD, SECRET_KEY and (when set) ADMIN_DASHBOARD_TOKEN
## against the running stack, then `make up`. The old SECRET_KEY becomes
## SECRET_KEY_PREVIOUS, so nobody is signed out. Never change SECRET_KEY by
## hand. FORCE=1 rotates inside the refresh-token overlap window.
rotate-secrets:
	@./scripts/rotate-secrets.sh $(if $(FORCE),--force,)

## Daily cron check that backs up, then rotates, once
## SECRET_ROTATION_INTERVAL_DAYS have passed. Runs at 06:20 host time.
rotate-cron:
	SCHEDULE="$(SCHEDULE)" ./scripts/install-rotate-cron.sh

## Remove the scheduled rotation cron entry.
rotate-cron-remove:
	./scripts/install-rotate-cron.sh --uninstall

## Show what the scheduled rotations have been doing.
rotate-cron-log:
	@tail -n 60 .secret-rotation/rotate.log 2>/dev/null || echo "No scheduled rotation has run yet."

## Clear the admin console's failed sign-in count and lockout.
unlock-admin:
	@$(COMPOSE) exec -T postgres sh -c \
		'psql -X -q -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c "UPDATE admin_security SET failed_attempts = 0, locked_until = NULL WHERE id = 1"'
	@echo "unlock-admin: console lockout cleared"

## Create the store reviewer account, or reset its password. Prompts for the
## password; it is an ordinary verified account with no special rights.
seed-reviewer-account:
	@test -n "$(EMAIL)" || (echo "pass EMAIL=reviewer@example.com" >&2; exit 1)
	$(COMPOSE) exec web python -m app.seed_reviewer "$(EMAIL)"
