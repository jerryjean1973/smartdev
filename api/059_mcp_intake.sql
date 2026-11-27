ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS admission text
    CHECK (admission IN ('created','updated','duplicate','message','reply'));
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS work_key text;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS matched_request uuid;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS instruction_hash text;
CREATE TABLE IF NOT EXISTS mcp_task_outbox (
    project_id uuid NOT NULL, request_id uuid NOT NULL, grant_id uuid REFERENCES external_mcp_grant(id),
    envelope bytea NOT NULL, next_attempt timestamptz NOT NULL DEFAULT now(), attempts integer NOT NULL DEFAULT 0,
    PRIMARY KEY(project_id,request_id),
    FOREIGN KEY(project_id,request_id) REFERENCES mcp_task_request(project_id,request_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS mcp_outbox_due ON mcp_task_outbox(next_attempt);
