CREATE TABLE IF NOT EXISTS smd_deploy_task (
    project_id uuid PRIMARY KEY REFERENCES projects(id),
    id uuid NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('queued','processing','deployed','failed','needs_attention')),
    claim_digest text,
    instance_id uuid UNIQUE,
    observed_revision text,
    note text,
    queued_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
