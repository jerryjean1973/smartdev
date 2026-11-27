CREATE TABLE IF NOT EXISTS smd_deploy_receipt (
    task_id uuid PRIMARY KEY REFERENCES smd_deploy_task(id),
    instance_id uuid NOT NULL UNIQUE,
    public_key bytea NOT NULL CHECK (octet_length(public_key) = 32),
    signature bytea CHECK (octet_length(signature) = 64),
    verified_at timestamptz,
    CHECK ((signature IS NULL) = (verified_at IS NULL))
);
