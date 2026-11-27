CREATE TABLE IF NOT EXISTS smd_deployment (
    project_id uuid PRIMARY KEY REFERENCES projects(id),
    id uuid NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('queued','creating','installing','ready','failed','unconfirmed')),
    instance_id uuid UNIQUE,
    callback_digest text,
    revision text NOT NULL,
    observed_revision text,
    error text,
    reported_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    checked_at timestamptz,
    installer_url text NOT NULL,
    installer_digest text NOT NULL
);
