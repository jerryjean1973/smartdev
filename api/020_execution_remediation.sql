ALTER TABLE conversation_execution ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'read-only'
    CHECK (mode IN ('read-only', 'workspace-write'));
ALTER TABLE conversation_submission ADD COLUMN IF NOT EXISTS released boolean NOT NULL DEFAULT false;
