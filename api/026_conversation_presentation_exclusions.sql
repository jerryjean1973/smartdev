CREATE TABLE IF NOT EXISTS conversation_presentation_exclusion (
    execution_id uuid PRIMARY KEY REFERENCES conversation_execution(id),
    provenance text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
