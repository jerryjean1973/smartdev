ALTER TABLE conversation_submission
    ADD COLUMN IF NOT EXISTS task_project_id uuid REFERENCES projects(id),
    ADD COLUMN IF NOT EXISTS task_payload_sha256 text;
CREATE UNIQUE INDEX IF NOT EXISTS conversation_submission_task_project_request
    ON conversation_submission(task_project_id, request_id)
    WHERE task_project_id IS NOT NULL;
