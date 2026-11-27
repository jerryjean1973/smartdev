ALTER TABLE smd_deploy_task
    ADD COLUMN IF NOT EXISTS project_manifest jsonb,
    ADD COLUMN IF NOT EXISTS target_tenant text,
    ADD COLUMN IF NOT EXISTS dns_zone text,
    ADD COLUMN IF NOT EXISTS deployment_hostname text,
    ADD COLUMN IF NOT EXISTS coder_hostname text,
    ADD COLUMN IF NOT EXISTS ssh_key_id uuid;

CREATE INDEX IF NOT EXISTS smd_deploy_task_project_manifest
    ON smd_deploy_task USING gin (project_manifest jsonb_path_ops);
