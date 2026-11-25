ALTER TABLE operator_task ADD COLUMN IF NOT EXISTS mig_signature text
    CHECK (mig_signature ~ '^[0-9a-f]{128}$');
