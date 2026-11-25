CREATE TABLE IF NOT EXISTS workspace_execution (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    branch text NOT NULL,
    head_before text,
    head_after text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    exit_code integer,
    context_note_ids uuid[] NOT NULL
);
CREATE INDEX IF NOT EXISTS workspace_execution_project_idx ON workspace_execution(project_id, started_at DESC);
