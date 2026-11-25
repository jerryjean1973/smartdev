CREATE TABLE IF NOT EXISTS project_runtime (
    project_id uuid NOT NULL REFERENCES projects(id),
    environment text NOT NULL CHECK(environment IN ('DEV','QA','PREPROD','PROD')),
    secret_bindings jsonb NOT NULL DEFAULT '{}',
    observed jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(project_id,environment)
);
