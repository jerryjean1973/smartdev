CREATE TABLE IF NOT EXISTS integration_deploy_key (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    repository text NOT NULL,
    remote_id bigint,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','registered','verified','revoking','revoked')),
    checked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS integration_deploy_key_project_idx ON integration_deploy_key(project_id);
