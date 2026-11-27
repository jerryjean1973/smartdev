ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS execution_phase text CHECK (execution_phase IN ('Processing','Processed'));
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS execution_code integer;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS waiting_for_input boolean NOT NULL DEFAULT false;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS execution_updated_at timestamptz;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS owner_done_at timestamptz;
CREATE TABLE IF NOT EXISTS workspace_screenshot (
 project_id uuid NOT NULL REFERENCES projects(id), digest text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), content bytea NOT NULL,
 PRIMARY KEY(project_id,digest)
);
