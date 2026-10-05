-- Operations (2026-10-05).
--
-- backup_runs: scripts/backup-db.sh records each verified dump here, so the
-- API can tell the operator when backups have silently stopped. The dump files
-- live on the host, where the containers cannot see them.
CREATE TABLE IF NOT EXISTS backup_runs (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS backup_runs_finished_at ON backup_runs (finished_at DESC);

-- admin_security: one row holding the admin console's failed sign-in count
-- and lockout, shared by every API worker and surviving restarts.
CREATE TABLE IF NOT EXISTS admin_security (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until TIMESTAMPTZ,
    last_failed_at TIMESTAMPTZ
);

INSERT INTO admin_security (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ops_alert_state: what the operations monitor last mailed about, so an alert
-- is mailed when it starts, repeated at most every OPS_ALERT_REPEAT_HOURS, and
-- followed by one "resolved" mail when it clears.
CREATE TABLE IF NOT EXISTS ops_alert_state (
    subject TEXT PRIMARY KEY,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_notified_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);
