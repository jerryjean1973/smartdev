ALTER TABLE objective_run
    ADD COLUMN IF NOT EXISTS changed_files integer CHECK (changed_files >= 0);
