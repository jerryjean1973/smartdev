CREATE TABLE IF NOT EXISTS mcp_task_request (
    project_id uuid NOT NULL REFERENCES projects(id),
    request_id uuid NOT NULL,
    task_id uuid,
    message_id uuid,
    execution_ref text,
    kind text NOT NULL CHECK (kind IN ('create','message','reply','submission')),
    received_at timestamptz,
    instruction text,
    title text,
    payload_digest text,
    delivery text NOT NULL CHECK (delivery IN ('received','accepted','unconfirmed')),
    PRIMARY KEY (project_id,request_id)
);
CREATE INDEX IF NOT EXISTS mcp_task_request_execution ON mcp_task_request(project_id,execution_ref);
CREATE INDEX IF NOT EXISTS mcp_task_request_task ON mcp_task_request(project_id,task_id,received_at DESC);
