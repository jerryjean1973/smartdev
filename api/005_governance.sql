CREATE TABLE IF NOT EXISTS objective_contract (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    original_request text NOT NULL,
    expected_outcome text NOT NULL,
    criteria jsonb NOT NULL,
    scope text[] NOT NULL,
    constraints text[] NOT NULL,
    dependencies text[] NOT NULL,
    version integer NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','RUNNING','BLOCKED','FAILED','COMPLETED')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS objective_project_idx ON objective_contract(project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS objective_revision (
    objective_id uuid NOT NULL REFERENCES objective_contract(id),
    version integer NOT NULL,
    definition jsonb NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objective_id, version)
);
CREATE TABLE IF NOT EXISTS objective_checkpoint (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    objective_id uuid NOT NULL REFERENCES objective_contract(id),
    version integer NOT NULL,
    criterion_id text NOT NULL,
    fingerprint text NOT NULL,
    evidence text NOT NULL CHECK (length(trim(evidence)) > 0),
    decision text NOT NULL CHECK (decision IN ('verified','blocked')),
    invalidated_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS objective_checkpoint_idx
    ON objective_checkpoint(objective_id, version, criterion_id, id DESC);
CREATE TABLE IF NOT EXISTS objective_run (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    objective_id uuid NOT NULL REFERENCES objective_contract(id),
    version integer NOT NULL,
    criterion_id text NOT NULL,
    agent text NOT NULL,
    worktree text NOT NULL,
    branch text NOT NULL,
    head text,
    fingerprint text NOT NULL,
    final_fingerprint text,
    started_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz NOT NULL DEFAULT now() + interval '90 seconds',
    finished_at timestamptz,
    exit_code integer
);
CREATE INDEX IF NOT EXISTS objective_run_project_idx ON objective_run(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS objective_run_criterion_idx ON objective_run(objective_id, version, criterion_id, started_at DESC);
