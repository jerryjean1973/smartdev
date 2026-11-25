ALTER TABLE workspace_execution ADD COLUMN IF NOT EXISTS changed_files text[] NOT NULL DEFAULT '{}';
