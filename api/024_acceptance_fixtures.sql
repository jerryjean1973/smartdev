CREATE TABLE IF NOT EXISTS acceptance_fixture (
    project_id uuid PRIMARY KEY REFERENCES projects(id),
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL UNIQUE,
    token_digest text NOT NULL CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    FOREIGN KEY (user_id, project_id, conversation_id)
        REFERENCES project_conversation(user_id, project_id, id)
);
