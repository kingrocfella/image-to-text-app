-- Rate limits move from Redis to PostgreSQL, as in Letterbolt, Lost Vowels and
-- NoAlibi: one atomic fixed-window counter per hashed key, shared by every API
-- worker. Rows past their window are deleted lazily.
CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    key TEXT PRIMARY KEY,
    count INTEGER NOT NULL CHECK (count > 0),
    reset_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rate_limit_buckets_reset_at
    ON rate_limit_buckets (reset_at);
