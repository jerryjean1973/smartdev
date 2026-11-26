CREATE TABLE IF NOT EXISTS host_installation (
    project_id uuid PRIMARY KEY REFERENCES projects(id),
    operation_id uuid NOT NULL UNIQUE,
    handoff jsonb NOT NULL,
    host_public_key text NOT NULL,
    expires_at timestamptz NOT NULL,
    claimed_at timestamptz,
    result jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS host_installation_vultr_instance
    ON host_installation ((result->'receipt'->>'vultr_instance_id'))
    WHERE result->'receipt'->>'outcome' = 'installed';
