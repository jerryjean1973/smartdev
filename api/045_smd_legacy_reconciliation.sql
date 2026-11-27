ALTER TABLE smd_deploy_task
    ADD COLUMN IF NOT EXISTS reconciled_at timestamptz,
    ADD COLUMN IF NOT EXISTS reconciliation jsonb;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conrelid = 'smd_deploy_task'::regclass
            AND conname = 'smd_deploy_task_pkey'
    ) THEN
        ALTER TABLE smd_deploy_task DROP CONSTRAINT smd_deploy_task_pkey;
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS smd_deploy_task_active_project
    ON smd_deploy_task(project_id) WHERE reconciled_at IS NULL;
