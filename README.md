# ScanGenAI API

The server for the ScanGenAI mobile app (`../image-text-react`): OCR for images, transcription for
audio, and question answering over an uploaded PDF. FastAPI, PostgreSQL, Redis (job queue), Qdrant
(PDF embeddings), and a Dramatiq worker.

**Read [../AGENTS.md](../AGENTS.md) before changing anything.** It is the enforceable rule set;
[../memory.md](../memory.md) records decisions, traps and what has actually been verified.

| | |
| --- | --- |
| Configuration | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| Running, backups, rotation, alerts | [docs/operations.md](docs/operations.md) |
| What is protected and why | [docs/security.md](docs/security.md) |
| Free plan, ScanGenAI Pro, store and sign-in setup | [docs/billing.md](docs/billing.md) |
| Which AI models, their prices, the free allowance | [docs/models.md](docs/models.md) |

## Quick start

```bash
make init-env     # writes .env (mode 0600) with generated secrets; refuses to overwrite
                  # then fill in SMTP_* and OPENAI_API_KEY
make up           # postgres + qdrant + redis + web + worker
make ps           # the API is "healthy" once it has migrated the database
make help         # everything else
```

The API listens on `127.0.0.1:${API_HOST_PORT}` only; a TLS reverse proxy is the public face.

## How a request flows

1. The app uploads a file to a `/v1` route. The API validates it by content, spends one unit of
   the account's monthly allowance, queues a Dramatiq job and answers `202` with a `message_id`.
2. The worker runs the job (PaddleOCR, Whisper, or retrieval over Qdrant plus a model).
3. The app polls `GET /v1/job/{message_id}`; only the account that queued a job can read it.

## Layout

```
app/
  config.py        the only module that reads the environment
  paths.py         every route path; the app's mirror is generated from it
  main.py          middleware, routers, exception handlers
  lifespan.py      startup (tables + migrations) and the background loops
  routes/          thin HTTP handlers
  services/        quota, job bookkeeping, operations monitor, admin auth, client logs
  queues/          Dramatiq actors and job status
  workers/         OCR, transcription and RAG implementations
  database/        engine, models, migration runner
  utils/           auth, email, uploads, rate limiting, logging
migrations/        append-only SQL, applied at startup
scripts/           env, backups, secret rotation, route generation, quality ratchet
tests/
```

## Checks

```bash
make install-dev    # API-layer tooling; the multi-gigabyte ML stack is mocked in tests
TEST_DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:5432/throwaway make check
```

`make check` = env check, quality ratchet, route-mirror check, tests, dependency audit. There is
no CI; this is the gate.
