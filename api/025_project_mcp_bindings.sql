CREATE TABLE IF NOT EXISTS project_mcp_binding (
  project_id uuid NOT NULL REFERENCES projects(id),
  name text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,47}$'),
  configuration jsonb NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  revision bigint NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'CONFIGURED',
  checked_at timestamptz,
  PRIMARY KEY (project_id, name)
);
