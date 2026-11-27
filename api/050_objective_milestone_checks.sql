ALTER TABLE objective_milestone
    ADD COLUMN IF NOT EXISTS checks jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(checks) = 'array');
