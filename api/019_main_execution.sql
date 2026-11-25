ALTER TABLE conversation_execution ADD COLUMN IF NOT EXISTS exit_code integer;
ALTER TABLE conversation_execution ADD COLUMN IF NOT EXISTS waiting_for_input boolean NOT NULL DEFAULT false;
