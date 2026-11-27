ALTER TABLE operator_task
    ADD COLUMN IF NOT EXISTS workspace text,
    ADD COLUMN IF NOT EXISTS revision text,
    ADD COLUMN IF NOT EXISTS release_lease_until timestamptz,
    ADD COLUMN IF NOT EXISTS deployed_revision text;
ALTER TABLE operator_task DROP CONSTRAINT IF EXISTS operator_task_operation_check;
ALTER TABLE operator_task ADD CONSTRAINT operator_task_operation_check CHECK (
    (operation='codex.version' AND workspace IS NULL AND revision IS NULL)
    OR (operation='smartdev.dev.release' AND workspace IS NOT NULL AND revision IS NOT NULL
        AND workspace ~ '^p-[a-z2-7]{26}$' AND revision ~ '^[0-9a-f]{40}$')
);
