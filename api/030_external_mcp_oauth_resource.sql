ALTER TABLE external_mcp_oauth_code ADD COLUMN IF NOT EXISTS resource text;
ALTER TABLE external_mcp_grant ADD COLUMN IF NOT EXISTS resource text;
