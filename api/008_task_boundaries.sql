CREATE TABLE IF NOT EXISTS objective_presentation (
    objective_id uuid PRIMARY KEY REFERENCES objective_contract(id),
    placement text NOT NULL CHECK (placement IN ('conversation','step')),
    reason text NOT NULL,
    classified_at timestamptz NOT NULL DEFAULT now()
);

-- Verified Browser validation prompts emitted by the old automatic router.
-- These remain addressable by their original objective IDs.
INSERT INTO objective_presentation(objective_id,placement,reason)
SELECT c.id, changes.placement, changes.reason
FROM (VALUES
    ('bd91a313-a375-403a-be69-a7aead3b20b0'::uuid, 'step', 'Browser validation conversation inspection step'),
    ('8740da36-4edb-4bc4-9d6b-077b7ba01471'::uuid, 'conversation', 'Browser validation automatic read-only subject routing'),
    ('1d74f590-16b7-4371-b69d-9f6f40a02ce5'::uuid, 'conversation', 'Browser validation automatic read-only subject routing'),
    ('ef63b15a-5873-4946-85a5-72083b2c2111'::uuid, 'conversation', 'Browser validation automatic read-only subject routing')
) AS changes(id,placement,reason)
JOIN objective_contract c ON c.id=changes.id
JOIN projects p ON p.id=c.project_id
WHERE p.name='Browser validation'
  AND (c.original_request LIKE 'Now investigate this different subject: report the Git branch name.%'
       OR c.original_request='Also check that the README exists as a separate subtask.')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS conversation_execution (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES project_conversation(id),
    expires_at timestamptz NOT NULL,
    finished_at timestamptz
);
