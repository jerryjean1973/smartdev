CREATE TABLE IF NOT EXISTS smd_environment_fix_request (
    id uuid PRIMARY KEY,
    grant_id uuid NOT NULL REFERENCES external_mcp_grant(id),
    client_request_id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES command_center_tenant(id),
    deployment_id uuid NOT NULL REFERENCES smd_deploy_task(id),
    project_id uuid NOT NULL REFERENCES projects(id),
    environment text NOT NULL CHECK (environment IN ('DEV','QA','DEPLOYED')),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 100),
    instruction text NOT NULL CHECK (length(instruction) BETWEEN 1 AND 30000),
    payload_digest text NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','claimed','delivered','denied','unconfirmed')),
    claim_id uuid,
    claimed_at timestamptz,
    receipt jsonb,
    failure_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(grant_id,client_request_id)
);
CREATE INDEX IF NOT EXISTS smd_environment_fix_pending
    ON smd_environment_fix_request(created_at,id)
    WHERE status IN ('queued','claimed');
