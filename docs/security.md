# Security posture

What is in place, and why. The rules that keep it that way are in
[../../AGENTS.md](../../AGENTS.md); how to operate it is in [operations.md](operations.md).

## Configuration fails closed

`app/config.py` is the only reader of the environment and refuses to start on a malformed value.
`ENVIRONMENT` must be exactly `dev` or `production`: before, anything other than `prod` or
`production` silently meant "not production", which dropped HSTS without a word. Production
additionally requires an https `APP_URL`, working SMTP settings and file logging, and refuses a
short admin console path. A `SECRET_KEY` shorter than 32 characters, or a placeholder, is refused
everywhere.

## Sessions

- Access tokens (1 hour) and refresh tokens (30 days) are JWTs carrying issuer, audience, subject,
  issued-at, type and a unique ID; all are required at verification.
- A refresh token works once. Each use rotates it under a row lock; presenting a spent or unknown
  token revokes its whole family.
- Logout blacklists the access token's SHA-256 and revokes the refresh family. Only fingerprints
  are stored, never tokens.
- A password reset stamps `password_changed_at`; every access token issued before it is rejected,
  and every refresh session is revoked.
- `SECRET_KEY` rotates without signing anyone out: the old key becomes `SECRET_KEY_PREVIOUS`,
  accepted for verification only.
- Expired blacklist rows and refresh sessions are deleted hourly; they can no longer change an
  answer.

## Accounts

- Registration, resend-verification and forgot-password each answer identically whether or not the
  address has an account. Login runs a bcrypt comparison even for an unknown address, so timing
  says nothing either.
- Login tells a caller the account is unverified only after the password has been checked. That
  reveals nothing to someone who does not already know the password, and lets the app offer to
  resend the email.
- Verification links expire (24 hours) and reset links expire (60 minutes) and work once. Only
  their SHA-256 is stored. The new password is chosen on a page this server renders, so no reset
  token passes through the app.
- Deleting an account requires fresh proof (the password, or a new Google / Apple token), removes the vector collections before the
  ownership records, blocks in-flight jobs, and fails closed if any store cannot be reached.

## Request boundary

- One default-deny router: every content route requires a verified session; the public surface is
  the auth routes, health, and the emailed-link pages.
- Body size and a request deadline are enforced by a pure ASGI layer before any handler runs.
  Security headers (CSP `default-src 'none'`, `nosniff`, `DENY`, no-referrer, HSTS in production)
  are set once. The three HTML pages carry their own nonce-based CSP.
- The interactive API docs are served outside production only.
- `X-App-Version` on every call; below `MINIMUM_APP_VERSION` the API answers 426.

## Rate limits

PostgreSQL-backed fixed windows (`rate_limit_buckets`), shared by every API process, keyed by a
SHA-256 so the table holds no IP address or user ID. Authenticated routes count per user,
anonymous ones per IP. If the counter cannot be written the request is refused (503), not waved
through.

**Client IP.** Uvicorn's own proxy-header handling is off. With `TRUST_PROXY_HEADERS=true` the
limiter reads the **last** `X-Forwarded-For` entry — the one the reverse proxy added. The first
entry is whatever the client sent; trusting it (as `--forwarded-allow-ips "*"` did) let anyone
choose their own rate-limit bucket. This is right with exactly one proxy in front. A CDN in front
of that proxy would need its own header.

| Route | Limit |
| --- | --- |
| register, resend-verification, forgot-password | 5/hour per IP |
| login | 20/hour per IP |
| refresh | 120/hour per IP |
| reset-password page / form | 30/hour, 10/hour per IP |
| image scan, PDF question | 60/hour per user |
| audio transcription | 30/hour per user |
| delete account | 10/hour per user |
| client logs | 30/minute per user |

## Allowances

Rate limits stop bursts; allowances stop a slow drain. OCR and transcription cost this server CPU,
every PDF question costs OpenAI embeddings, and a cloud-model answer costs that provider too — all
billed to the operator. Each account gets a monthly allowance per kind of work
(`usage_counters`), spent atomically in the request's own transaction so a failed enqueue gives it
back. The server also decides which models exist (`GET /v1/me`); a model with no provider key
cannot be requested.

This replaced `OPENAI_PASS`, one shared password typed into the app: it could not be revoked for
one person, and Gemini and DeepSeek had no gate at all.

## Uploads

Validated by content, not by extension or MIME type: images must decode, within pixel and frame
bounds; PDFs must parse, unencrypted, within a page bound; audio must match a known container
signature. Each is read incrementally against its byte limit, and the worker re-checks PDFs at the
sink. Temporary files are deleted on every path, including a refused allowance.

## Logs

Redaction happens in the formatter, the last step before a sink, so exception text and tracebacks
are covered: emails, JWTs, bearer credentials, `token=` parameters and upload paths. Requests are
logged as method, path, status and duration — never the query string. Job failures return one
generic message to the client; the detail stays in `errors.log`.

Mobile logs arrive at `POST /v1/client-logs`: session required, rate limited, bounded, and
re-sanitised here even though the app already redacts — the payload is untrusted. The user's
questions, file names and extracted text are redacted by key on both sides.

## Admin console

Read-only. Not registered at all while `ADMIN_DASHBOARD_TOKEN` is empty. Username and token are
compared in constant time, always both. Failed attempts are counted in PostgreSQL, so restarting
the process does not reset the lock; a locked console refuses before comparing. Sessions are
signed, HTTP-only, SameSite=Strict cookies that expire when idle. Responses are `no-store` and
`noindex`.

## Containers

Digest-pinned images; the API and worker run as a non-root user with a read-only root filesystem,
all capabilities dropped, `no-new-privileges`, PID limits and tmpfs. Every published port binds to
127.0.0.1. Docker's own log files are capped.

## The mobile app

- Reads no environment variables; everything in `src/constants` is public by definition.
- The session is in the platform keystore with device-only accessibility, never AsyncStorage.
- Every request has a timeout; job polling is bounded and stops when the screen is left.
- `npm run check` enforces: no environment reads, no `console.*` outside the logger, no route
  literals, and a strict production dependency audit.

## Social sign-in

Google (Android) and Apple (iOS). The app obtains the provider's token and the server verifies it:
Google's against the Web client ID, Apple's against Apple's published keys and the bundle ID.
Accounts are keyed by the provider's subject, never by email. A first sign-in links to an existing
account with the same address only when the provider says the address is verified; if that
existing account had never verified its email, its password is discarded, so nobody can
pre-register someone else's address and keep a way in. An account made this way has no password,
and deleting it requires a fresh token for the linked identity.

## Plans

The server decides who is on ScanGenAI Pro, from store receipts it has verified itself
([billing.md](billing.md)); the app's opinion is never consulted, and a failed read means "free".
OpenAI cannot be configured as a free model: startup refuses it.

## Deliberately not done

- **App attestation** (App Attest / Play Integrity). The sibling games use it against score
  cheating. Here the abuse is spending the operator's money, which allowances bound per account.
  Same decision as NoAlibi (owner, 28 September 2026).

## Known limits

- The request deadline answers 504 but cannot cancel work already running in a thread.
- Account deletion across PostgreSQL, Qdrant and Redis is not one transaction; it is ordered to
  fail towards deleting data rather than towards keeping it.
- A user can be rate limited by someone else behind the same NAT on the per-IP routes.
