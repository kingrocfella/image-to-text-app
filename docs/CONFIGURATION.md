# Configuration

`.env` in this directory is the only environment file in the workspace. It is gitignored and
mode 0600. `make init-env` writes it with safe local defaults (and refuses to overwrite one);
`make check-env` fails when a key the code reads is missing or duplicated, when a key is there
that nothing reads, when a second env file exists, or when anything outside `app/config.py`
reads the environment.

`app/config.py` parses and validates every value at startup and stops the process, listing every
problem, if one is malformed or unsafe for production. Real process variables win over the file.

To add a setting: declare it in `Settings` and `load_settings()` in `app/config.py`, add it with a
safe default to the template in `scripts/init-env.sh`, and add it to `.env` — in the same change.

## Runtime

| Key | Default | Meaning |
| --- | --- | --- |
| `ENVIRONMENT` | `dev` | `dev` or `production`, nothing else. A typo stops startup. |
| `APP_URL` | — | Public origin for emailed links. No path. Required, and https, in production. |
| `CORS_ALLOWED_ORIGINS` | empty | Comma-separated browser origins. The mobile app needs none. `*` is refused. |
| `MINIMUM_APP_VERSION` | `0.0.0` | Oldest app version served; older builds get 426. See operations.md. |
| `MAX_REQUEST_BODY_BYTES` | 25 MB | Ceiling for any request body. |
| `REQUEST_TIMEOUT_SECONDS` | 30 | Deadline for a request; answers 504. |
| `TRUST_PROXY_HEADERS` | `false` | `true` behind the reverse proxy: rate limits key on the last `X-Forwarded-For` entry. |
| `API_HOST_PORT` | 8000 | Host port Compose publishes the API on (127.0.0.1 only). |

## Logging

| Key | Default | Meaning |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | |
| `LOG_TO_FILE` | `false` | Also write `info.log` and `errors.log`. Compose sets it; production requires it. |
| `LOG_DIR` | `logs` | Compose overrides it to `/app/logs` (a named volume). |
| `LOG_MAX_SIZE_MB`, `LOG_BACKUP_COUNT` | 10, 5 | Rotation of each file. |

## Datastores

| Key | Default | Meaning |
| --- | --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT` | — | Required. `POSTGRES_*` only initialise a *new* volume; change the password with `make rotate-secrets`. |
| `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`, `QDRANT_HOST_PORT` | 5433, 6382, 6333 | Host ports (127.0.0.1 only). Allocated across the shared VPS; check the other apps first. |
| `TEST_DATABASE_URL` | empty | Throwaway PostgreSQL for the tests. Never the application database. |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` | `redis`, 6379, 0 | The Dramatiq job queue and result store. |
| `QDRANT_URL` | `http://qdrant:6333` | PDF embeddings. |
| `WORKER_THREADS` | 8 | Dramatiq worker threads. |

## Auth

| Key | Default | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | — | Signs tokens. 32+ characters, no placeholder. Rotate only with `make rotate-secrets`. |
| `SECRET_KEY_PREVIOUS` | empty | The key before the last rotation; verification only. |
| `SECRET_ROTATION_INTERVAL_DAYS` | 90 | Scheduled rotation interval; keep it at least `REFRESH_TOKEN_EXPIRE_DAYS`. |
| `JWT_ISSUER`, `JWT_AUDIENCE` | `scangenai-api`, `scangenai-client` | Bound into, and required of, every token. |
| `ACCESS_TOKEN_EXPIRE_HOURS` | 1 | |
| `REFRESH_TOKEN_EXPIRE_DAYS` | 30 | How long a phone stays signed in without being opened. |
| `VERIFICATION_TOKEN_EXPIRE_HOURS` | 24 | |
| `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | 60 | |
| `SECURITY_PURGE_INTERVAL_SECONDS` | 3600 | How often expired blacklist rows, refresh sessions and old job records are deleted. |

## Email

`SMTP_SERVER`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_NAME`. Required in
production: verification, password reset and operator alerts all send mail.

## Uploads and retention

| Key | Default |
| --- | --- |
| `IMAGE_MAX_BYTES`, `IMAGE_MAX_PIXELS`, `IMAGE_MAX_FRAMES` | 10 MB, 40 MP, 20 |
| `AUDIO_MAX_BYTES` | 20 MB |
| `PDF_MAX_BYTES`, `PDF_MAX_PAGES` | 20 MB, 100 |
| `RAG_RETENTION_DAYS` | 30 — PDF embeddings are deleted this long after upload |
| `JOB_TYPE_TTL_DAYS` | 7 — job results and ownership metadata in Redis |

The app repeats the three byte limits (`UPLOAD_LIMITS` in its `src/constants`) only to warn before
a long upload. Change both together.

## Models and allowances

Reasoning and prices: [models.md](models.md). Plans: [billing.md](billing.md).

| Key | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_URL`, `OLLAMA_MODEL` | `http://ollama:11434`, `llama3.2:3b` | The shared Lost Vowels daemon. |
| `OLLAMA_TEMPERATURE`, `OLLAMA_NUM_PREDICT`, `OLLAMA_TIMEOUT_SECONDS` | 0.7, 500, 300 | |
| `OLLAMA_KEEP_ALIVE`, `OLLAMA_HOST_PORT` | `10m`, 11438 | The dev-only sidecar profile. |
| `OPENAI_API_KEY` | empty | Needed for **every** PDF question (embeddings) and the `openai` model. Empty turns PDF questions off. |
| `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY` | empty | Empty means that model is not offered. |
| `OPENAI_MODEL` | `gpt-6-luna` | |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Newest model on Google's free tier. |
| `DEEPSEEK_MODEL` | `deepseek-flash` | |
| `CLAUDE_MODEL` | `claude-haiku-4-5` | |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | For new PDFs; each stored PDF remembers its own. |
| `RAG_TOP_K` | 8 | Passages of the PDF sent with each question (1–50). |
| `CLOUD_MODEL_MAX_OUTPUT_TOKENS` | 800 | Cap on an answer's length. |
| `FREE_CLOUD_MODELS` | `gemini,deepseek` | Cloud models a free account may use. Never `openai`. |
| `QUOTA_IMAGE_MONTHLY`, `QUOTA_SOUND_MONTHLY`, `QUOTA_PDF_MONTHLY`, `QUOTA_CLOUD_MODEL_MONTHLY` | 100, 30, 50, 10 | Free plan, per user per UTC month. 0 turns it off. |
| `PRO_QUOTA_IMAGE_MONTHLY`, `PRO_QUOTA_SOUND_MONTHLY`, `PRO_QUOTA_PDF_MONTHLY`, `PRO_QUOTA_CLOUD_MODEL_MONTHLY` | 1000, 300, 500, 300 | ScanGenAI Pro. |

## Subscriptions and sign-in

| Key | Default | Meaning |
| --- | --- | --- |
| `BILLING_PROVIDER` | `off` | `off`, `dev` (fake receipts; refused in production) or `store`. |
| `APPLE_ENVIRONMENT`, `APPLE_APP_ID`, `APPLE_ROOT_CA_PATH` | `Production`, 0, `certs/apple-root-ca-g3.pem` | Needed for `store`. |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | empty | The Play service account key, on one line. Needed for `store`. |
| `GOOGLE_WEB_CLIENT_ID` | empty | Audience of every Google ID token; must equal the app's. Empty disables Google sign-in. |
| `GOOGLE_OAUTH_TIMEOUT_S` | 5 | |
| `ANDROID_PACKAGE_NAME`, `IOS_BUNDLE_ID` | `com.leonfrontier.scangenai` | The bundle ID is also the audience of an Apple token. |

## Operations

| Key | Default | Meaning |
| --- | --- | --- |
| `ADMIN_DASHBOARD_TOKEN` | empty | Empty: the console is not registered at all. Otherwise 32+ characters. |
| `ADMIN_DASHBOARD_USERNAME`, `ADMIN_DASHBOARD_PATH` | `admin`, `/admin` | Production requires a path of 16+ characters. |
| `ADMIN_AUTO_LOGOUT_MINUTES`, `ADMIN_LOCK_THRESHOLD`, `ADMIN_LOCK_MINUTES` | 15, 5, 30 | |
| `NOTIFY_EMAILS_ENABLED`, `NOTIFY_EMAIL_TO` | `false`, empty | Operator alerts. Never mails users. |
| `BACKUP_MAX_AGE_HOURS` | 48 | Alert when the newest verified backup is older. |
| `OPS_CHECK_INTERVAL_MINUTES`, `OPS_ALERT_REPEAT_HOURS` | 60, 24 | |
