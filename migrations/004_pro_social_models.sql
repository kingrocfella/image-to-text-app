-- ScanGenAI Pro, social sign-in and per-collection embedding models (2026-10-05).
--
-- purchases: one row per store subscription, keyed by the store's stable ID
-- (Apple originalTransactionId, Google purchaseToken), so renewals, restores
-- and store notifications all update the same row. The rules for how rows
-- change are in app/services/billing/service.py.
CREATE TABLE IF NOT EXISTS purchases (
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL CHECK (platform IN ('ios', 'android')),
    product_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    user_id UUID REFERENCES users (id) ON DELETE CASCADE,
    purchased_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    revocation_reason TEXT,
    checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT purchases_store_subscription UNIQUE (platform, transaction_id)
);

CREATE INDEX IF NOT EXISTS purchases_user_active
    ON purchases (user_id, expires_at) WHERE revoked_at IS NULL;

-- pro_until: Pro granted without a purchase (support, a promotion, review).
ALTER TABLE users ADD COLUMN IF NOT EXISTS pro_until TIMESTAMPTZ;

-- Social sign-in. An account created through Google or Apple has no password;
-- identities are keyed by the provider's subject, never by email address.
ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS apple_sub VARCHAR(255);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_apple_sub ON users (apple_sub);

-- The embedding model that built each PDF collection. Collections that exist
-- already were built with text-embedding-3-large and stay searchable with it;
-- NULL means exactly that.
ALTER TABLE pdf_requests ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(64);
