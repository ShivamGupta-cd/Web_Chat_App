ALTER TABLE password_reset_tokens ADD COLUMN token_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_hash ON password_reset_tokens (token_hash, used_at, expires_at);
