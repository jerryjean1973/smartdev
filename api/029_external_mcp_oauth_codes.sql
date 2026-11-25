CREATE TABLE IF NOT EXISTS external_mcp_oauth_code (
    code_hash bytea PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES smartdev_owner(id),
    client_id text NOT NULL,
    redirect_uri text NOT NULL,
    challenge text NOT NULL,
    project_ids uuid[] NOT NULL CHECK (cardinality(project_ids)>0),
    scopes text[] NOT NULL CHECK (cardinality(scopes)>0 AND scopes <@ ARRAY['read','write']),
    expires_at timestamptz NOT NULL
);
