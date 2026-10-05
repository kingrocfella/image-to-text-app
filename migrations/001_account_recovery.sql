-- Account recovery and session invalidation (2026-10-05).
--
-- Tables used to be created only by SQLAlchemy's create_all, which never
-- alters a table that already exists. From here on every schema change is an
-- append-only file in this directory; startup creates missing model tables
-- first and then applies these, so each statement must also be a no-op on a
-- fresh database.
--
-- verification_expires_at: verification links used to stay valid for ever.
-- password_reset_*:        one-time, hashed, short-lived reset tokens.
-- password_changed_at:     access tokens issued before it are rejected.
ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token VARCHAR(64);
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_users_verification_token ON users (verification_token);
CREATE INDEX IF NOT EXISTS ix_users_password_reset_token ON users (password_reset_token);
CREATE INDEX IF NOT EXISTS ix_token_blacklist_expires_at ON token_blacklist (expires_at);
CREATE INDEX IF NOT EXISTS ix_refresh_sessions_expires_at ON refresh_sessions (expires_at);
