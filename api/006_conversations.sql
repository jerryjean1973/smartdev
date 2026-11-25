CREATE TABLE IF NOT EXISTS smartdev_owner (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    last_project_id uuid REFERENCES projects(id)
);
INSERT INTO smartdev_owner (singleton) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS project_conversation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES smartdev_owner(id),
    project_id uuid NOT NULL REFERENCES projects(id),
    name text NOT NULL,
    legacy boolean NOT NULL DEFAULT false,
    active_task_id uuid,
    codex_session_id uuid,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, project_id, id)
);
CREATE TABLE IF NOT EXISTS project_conversation_pointer (
    user_id uuid NOT NULL,
    project_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    version bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, project_id),
    FOREIGN KEY (user_id, project_id, conversation_id)
       REFERENCES project_conversation(user_id, project_id, id)
);
CREATE TABLE IF NOT EXISTS conversation_submission (
    conversation_id uuid NOT NULL REFERENCES project_conversation(id),
    request_id uuid NOT NULL,
    message_id uuid NOT NULL DEFAULT gen_random_uuid(),
    result jsonb,
    PRIMARY KEY (conversation_id, request_id)
);
