-- Enrollment is performed by the trusted operator after independent MIG approval.
-- No workspace or machine endpoint can create or extend enrollment.
CREATE TABLE IF NOT EXISTS operator_task (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    task_id uuid NOT NULL REFERENCES objective_contract(id),
    execution_id uuid NOT NULL REFERENCES objective_run(id),
    environment text NOT NULL CHECK (environment = 'dev'),
    operation text NOT NULL CHECK (operation = 'codex.version'),
    mig_receipt uuid NOT NULL UNIQUE,
    token_sha256 text NOT NULL CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    dispatch_id uuid UNIQUE,
    claim_id uuid UNIQUE,
    completion_id uuid UNIQUE,
    host_receipt_id uuid UNIQUE,
    outcome text CHECK (outcome IN ('observed','denied','expired','revoked','unconfirmed')),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS operator_task_event (
    id uuid PRIMARY KEY,
    approval_id uuid NOT NULL,
    project_id uuid NOT NULL,
    task_id uuid NOT NULL,
    execution_id uuid NOT NULL,
    phase text NOT NULL,
    outcome text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
