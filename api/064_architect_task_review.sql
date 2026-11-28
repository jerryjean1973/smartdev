CREATE TABLE IF NOT EXISTS architect_task_review (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    execution_id uuid NOT NULL,
    objective_id uuid,
    conversation_id uuid,
    plan_sha256 text NOT NULL CHECK (plan_sha256 ~ '^[a-f0-9]{64}$'),
    git_head text,
    model text NOT NULL,
    classification text NOT NULL CHECK (classification IN
        ('ARCHITECTURE_PRESERVING','ARCHITECTURE_IMPACTING','CANONICAL_CHANGE','REVIEW_REQUIRED')),
    coherence_approved boolean NOT NULL,
    constraints jsonb NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, execution_id),
    CHECK ((objective_id IS NULL) <> (conversation_id IS NULL)),
    CHECK (coherence_approved = (classification='ARCHITECTURE_PRESERVING'))
);

CREATE INDEX IF NOT EXISTS architect_task_review_project_time
    ON architect_task_review(project_id, reviewed_at DESC);
