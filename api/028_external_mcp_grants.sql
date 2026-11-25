CREATE TABLE IF NOT EXISTS external_mcp_grant (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES smartdev_owner(id),
    token_hash bytea NOT NULL UNIQUE,
    project_ids uuid[] NOT NULL CHECK (cardinality(project_ids) > 0),
    scopes text[] NOT NULL CHECK (cardinality(scopes) > 0 AND scopes <@ ARRAY['read', 'write']),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
