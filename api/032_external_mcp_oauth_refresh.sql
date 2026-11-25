ALTER TABLE external_mcp_grant
    ADD COLUMN IF NOT EXISTS refresh_token_hash bytea,
    ADD COLUMN IF NOT EXISTS refresh_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS refresh_client_id text,
    ADD COLUMN IF NOT EXISTS refresh_redirect_uri text;

CREATE UNIQUE INDEX IF NOT EXISTS external_mcp_grant_refresh_token
    ON external_mcp_grant(refresh_token_hash) WHERE refresh_token_hash IS NOT NULL;
