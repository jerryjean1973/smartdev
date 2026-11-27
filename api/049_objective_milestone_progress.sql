ALTER TABLE objective_milestone
    ADD COLUMN IF NOT EXISTS progress_completed smallint,
    ADD COLUMN IF NOT EXISTS progress_total smallint;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'objective_milestone'::regclass AND conname = 'objective_milestone_progress_valid'
    ) THEN
        ALTER TABLE objective_milestone ADD CONSTRAINT objective_milestone_progress_valid CHECK (
            (progress_completed IS NULL AND progress_total IS NULL)
            OR (progress_completed IS NOT NULL AND progress_total IS NOT NULL
                AND progress_total > 0 AND progress_completed BETWEEN 0 AND progress_total)
        );
    END IF;
END;
$$;
