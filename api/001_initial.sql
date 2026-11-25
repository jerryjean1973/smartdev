CREATE TABLE IF NOT EXISTS projects (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    repository text NOT NULL,
    runtime text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued','provisioning','created','failed')),
    workspace_url text,
    error text,
    archived boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    attempted_at timestamptz
);
CREATE TABLE IF NOT EXISTS audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    action text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
