# P0 security deployment checklist

The code-side P0 controls are enforced in this repository. The remaining steps operate on live
state and must be completed during a maintenance window by an operator with access to the real
secret store and production host.

## Before deployment

1. Stop the public API and worker so no new jobs or tokens are created during rotation.
2. Generate new, independent values for `SECRET_KEY` (at least 32 random bytes) and `OPENAI_PASS`
   (at least 16 random bytes). Do not reuse either old value or place the new values in shell
   history, source control, chat, or logs.
3. Update the deployment secret store, not `.env.example`. Rotating `SECRET_KEY` intentionally
   invalidates every existing access and refresh token.
4. Review `CORS_ALLOWED_ORIGINS`. Native clients need no CORS origin; any browser origins must be
   explicit HTTPS origins. Wildcards are rejected at startup.

## Purge potentially exposed historical state

Old RAG messages contained the submitted `openai_pass`, and old workers logged the full message.
After confirming that losing all queued/in-flight results is acceptable, purge the **dedicated
ScanGenAI Redis database**. Do not run `FLUSHDB` against a shared Redis database.

```sh
docker compose -f docker-compose.prod.yml exec redis redis-cli DBSIZE
docker compose -f docker-compose.prod.yml exec redis redis-cli FLUSHDB
```

Delete or securely rotate every application and Docker log that predates this deployment according
to the host's retention policy. Search only for the old secret's hash or a tightly scoped marker;
never print the old or new secret into a terminal transcript.

## Deploy and verify

1. Rebuild and deploy both `web` and `worker` from the same revision.
2. Confirm startup rejects a missing/placeholder `SECRET_KEY`, missing production `OPENAI_PASS`,
   wildcard CORS, or invalid database configuration.
3. Confirm host listeners for the API, PostgreSQL, Redis, Qdrant, and Ollama are loopback-only or
   absent. Only the intended TLS reverse proxy may listen publicly.
4. Sign in again, submit one job of each type, and verify:
   - the creator can poll the result;
   - a second account receives `404` for the same message ID;
   - Redis job metadata contains only `job_type` and `owner_user_id`;
   - the Dramatiq payload and new logs contain no `openai_pass`.
5. Verify an unknown API path returns a small JSON `404` response with no `Location` header.

Record the deployment time, operator, secret versions, Redis purge result, log-retention action,
and verification evidence in the private operations log.
