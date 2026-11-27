ALTER TABLE smd_deploy_receipt
    ADD COLUMN IF NOT EXISTS expected_ipv4 inet,
    ADD COLUMN IF NOT EXISTS connections jsonb;

ALTER TABLE smd_deploy_receipt
    ADD COLUMN IF NOT EXISTS enrolled_at timestamptz NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS smd_git_token_nonce (
    task_id uuid NOT NULL REFERENCES smd_deploy_task(id),
    nonce text NOT NULL CHECK (nonce ~ '^[a-f0-9]{64}$'),
    PRIMARY KEY (task_id, nonce)
);
