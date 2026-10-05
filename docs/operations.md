# Operations

The runbook for the ScanGenAI API. Every `make` command runs from `image-to-text-app/`; `.env`
there is the only environment file (see [CONFIGURATION.md](CONFIGURATION.md)).

## Running the stack

```bash
make up            # check-env, then build and start postgres + qdrant + redis + web + worker
make ps            # container status (the API has a Docker health check)
make logs          # follow the API's stdout;  make worker-logs for the worker
make log-files     # follow the saved info.log   (SERVICE=worker for the worker's)
make error-files   # follow the saved errors.log
make down          # stop; named volumes (database, vectors, logs) survive
```

The API only becomes **healthy** after it has connected to PostgreSQL and applied migrations, so a
wrong database password shows up as an unhealthy container, not a half-working one. `GET /ready`
additionally checks Redis. `make up` passes `--remove-orphans`, which also clears containers left
by an older Compose layout.

Ollama is not part of this stack on the VPS: the API and worker reach the single Lost Vowels
daemon over the `word-games-ollama` network. The `ollama` Compose profile is for a development
machine only.

## Quality gate

There is no CI. `make check` is the only gate and must pass before a change is done:

```bash
make install-dev                                  # API-layer tooling only; no ML stack
TEST_DATABASE_URL=postgresql+asyncpg://… make check
```

It runs check-env, the quality ratchet (flake8, mypy, black, isort against a recorded baseline),
check-routes (the app's route mirror matches `app/paths.py`), the tests, and `pip-audit` (zero
advisories, no exceptions). Without `TEST_DATABASE_URL` the tests run on SQLite and skip the
PostgreSQL-only ones (rate limiter, operations monitor, admin console, migrations) — set it to a
throwaway database before relying on the result. The mobile app's gate is `npm run check` in
`../image-text-react`.

## Migrations

Applied automatically at API startup, in filename order, each recorded in `schema_migrations`.
Startup first creates any model table that does not exist yet, then applies the files in
`migrations/`. So:

- a migration is **append-only** — never edit one that may have been applied;
- every statement must be a no-op on a fresh database (`IF NOT EXISTS`), because there the model
  already created the table in its final shape;
- a new column goes in **both** the model and a migration.

## Retiring old app builds

The app sends `X-App-Version` on every call. Any API request from a version below
`MINIMUM_APP_VERSION` gets **426**, and the app shows "Update required". A build from before `/v1`
sends no version and counts as 0.0.0. Health checks and emailed-link pages are never gated.

1. Ship the replacement build and wait until it is **live in both stores**.
2. Set `MINIMUM_APP_VERSION` in `.env` to the oldest version still supported, then `make up`.
3. To undo, set it back to `0.0.0` and `make up`.

Once no supported build calls the unversioned paths, delete the second `include_router` in
`app/main.py`.

## Backups

```bash
make backup                                            # one verified dump (+ Qdrant snapshot)
make backup-cron                                       # daily at 01:40 host time
make backup-log                                        # what the scheduled runs did
make restore BACKUP=/abs/path.dump CONFIRM=restore    # destructive
```

Each dump is checked with `pg_restore --list` and then **recorded in `backup_runs`** — that is how
the monitor and the admin console know backups are current, because the containers cannot see
`./backups`. A dump that cannot be recorded is still kept; the monitor then reports backups as
stale, which errs in the safe direction. Local files are not disaster recovery: copy them off the
host.

## Operations monitor

Runs in the API process every `OPS_CHECK_INTERVAL_MINUTES`. Alerts are always logged (critical
ones to `errors.log`) and stored in `ops_alert_state`. With `NOTIFY_EMAILS_ENABLED=true` an alert
is mailed to `NOTIFY_EMAIL_TO` when it starts, again every `OPS_ALERT_REPEAT_HOURS` while it
lasts, and once when it clears. It never mails users, and a failed notification never fails
anything else.

| Alert | Severity | Fires when | First thing to check |
| --- | --- | --- | --- |
| `backups` | critical | no verified backup recorded, or the newest is older than `BACKUP_MAX_AGE_HOURS` | `make backup-log`; `crontab -l`; disk space |
| `jobs-stuck` | critical | a job has been queued for more than 30 minutes | `make ps`; `make worker-logs` |
| `job-failures` | warning | 10+ jobs failed in 24 hours, or 25%+ of at least 20 | `make error-files SERVICE=worker`; the model providers; Qdrant |

Job outcomes come from the `job_runs` table (no user content; deleted with the account, and after
30 days).

## Allowances

Per user, per UTC calendar month, by plan: `QUOTA_*` for free accounts and `PRO_QUOTA_*` for
ScanGenAI Pro ([billing.md](billing.md); how the numbers were chosen is in [models.md](models.md)). A request over the limit gets 429 with an
`X-Quota-Exceeded` header and a message the app shows. Change a limit in `.env` and `make up`; it
applies at once, including to the current month. To give one account more, lower its counter:

```sql
UPDATE usage_counters SET count = 0
WHERE user_id = '…' AND period = to_char(now() AT TIME ZONE 'utc', 'YYYY-MM') AND kind = 'pdf';
```

A cloud model is offered only when its provider key is set, the plan's cloud allowance is above
0, and (on the free plan) it is named in `FREE_CLOUD_MODELS`. Without `OPENAI_API_KEY` no PDF
question can be answered at all (embeddings need it).

## Secret rotation

```bash
make rotate-secrets          # rotate, then make up
make rotate-secrets FORCE=1  # inside the overlap window (signs some sessions out)
make rotate-cron             # daily check at 06:20; backs up, then rotates every
                             # SECRET_ROTATION_INTERVAL_DAYS (90)
make rotate-cron-log         # what the scheduled runs did
make rotate-cron-remove
```

Rotates `POSTGRES_PASSWORD` (through `psql \password` on stdin, then verified over TCP),
`SECRET_KEY` and, when the console is enabled, `ADMIN_DASHBOARD_TOKEN`; then runs `make up` and
waits for the API to be healthy. The old `SECRET_KEY` becomes `SECRET_KEY_PREVIOUS`, which the API
accepts for verification only, so nobody is signed out. It refuses to rotate again within
`REFRESH_TOKEN_EXPIRE_DAYS` of the last rotation (that would drop the key live refresh tokens were
signed with) unless forced, and never prints a secret: read new values from `.env`.

**Never change `SECRET_KEY` by hand.** Provider keys (`OPENAI_API_KEY`, SMTP, …) are not rotated
here; replace them at the provider and edit `.env`.

Recovery: if the script reports that the PostgreSQL rollback failed, the new credentials are in
`.rotate-secrets/pending` (mode 0600). Either move that file over `.env`, or set the role back to
the old password with `\password` from `docker compose exec postgres psql`; then remove
`.rotate-secrets/` to unlock rotation.

## Admin console

Served at `ADMIN_DASHBOARD_PATH` once `ADMIN_DASHBOARD_TOKEN` is set (`openssl rand -hex 32`, and a
path like `/ops-$(openssl rand -hex 8)`). Read-only: open alerts, the newest backup, users,
sign-ups, jobs and how many accounts are at an allowance. After `ADMIN_LOCK_THRESHOLD` bad
sign-ins it locks for `ADMIN_LOCK_MINUTES`: `make unlock-admin`.

## Store reviewer account

```bash
make seed-reviewer-account EMAIL=review@example.com
```

Creates an ordinary, already-verified account, or resets its password; it prompts for the
password. Give the same sign-in to App Review and Play review.

## Deploying this release for the first time

This release changed configuration, storage and the Compose layout. On the VPS, once:

1. Take a backup with the **old** revision still running: `make backup`.
2. Update `.env` to the new key set. `make check-env` names every key that is missing and every
   key nothing reads any more (`APP_HOST`, `APP_PORT`, `APP_DEBUG` and `OPENAI_PASS` go). Compare
   with `scripts/init-env.sh`. In particular:
   - `ENVIRONMENT=production` (not `prod`);
   - `TRUST_PROXY_HEADERS=true`, and confirm the reverse proxy sets `X-Forwarded-For`;
   - `REFRESH_TOKEN_EXPIRE_DAYS=30`;
   - `LOG_TO_FILE=true`;
   - `OPENAI_API_KEY` must be present in `.env` itself — it is now read through the config module;
   - `ANTHROPIC_API_KEY` if Claude should be offered, and the four `*_MODEL` keys at the values
     in [models.md](models.md);
   - `BILLING_PROVIDER=off` until the store products exist ([billing.md](billing.md));
   - `GOOGLE_WEB_CLIENT_ID` once the Google OAuth clients exist.
3. `make up`. Startup adds the new columns and tables. Watch `make logs` for
   "Applied SQL migration".
4. Old rate-limit keys and any queued job from before this release can be dropped:
   `docker compose exec redis redis-cli FLUSHDB` (this Redis is dedicated to ScanGenAI; in-flight
   jobs are lost). Older queued PDF jobs may also contain the retired shared model password.
5. The worker now logs to its own `worker_logs` volume; the old `app.log` in `app_logs` is no
   longer written and can be deleted.
6. `make backup-cron` and `make rotate-cron`, then one `make backup` to confirm it is recorded
   (the `backups` alert clears on the next monitor run).
