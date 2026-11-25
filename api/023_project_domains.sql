CREATE TABLE IF NOT EXISTS project_domain (
    hostname text PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    challenge text NOT NULL,
    verified_at timestamptz,
    checked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS project_domain_project_idx ON project_domain(project_id);
