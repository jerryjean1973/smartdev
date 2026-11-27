ALTER TABLE smd_deployment_assignment
    ADD COLUMN IF NOT EXISTS portal_account text NOT NULL DEFAULT 'owner' CHECK (portal_account = 'owner'),
    ADD COLUMN IF NOT EXISTS portal_ready_instance uuid;

CREATE TABLE IF NOT EXISTS smd_portal_launch (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES smd_deploy_task(id) ON DELETE CASCADE,
    instance_id uuid NOT NULL,
    assignment_version integer NOT NULL,
    session_digest bytea NOT NULL,
    session_expires bigint NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '2 minutes'),
    challenge text,
    code_digest bytea
);
CREATE INDEX IF NOT EXISTS smd_portal_launch_expiry ON smd_portal_launch(expires_at);
