CREATE TABLE IF NOT EXISTS integration_connections (
    provider text PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('NOT_CONNECTED','AUTHORIZATION_REQUIRED','CONNECTED','CONFIGURATION_REQUIRED',
        'OPERATIONAL','DEGRADED','EXPIRED','FAILED','MANUAL_SETUP_REQUIRED')),
    message text NOT NULL DEFAULT '',
    items text[] NOT NULL DEFAULT '{}',
    checked_at timestamptz
);
CREATE TABLE IF NOT EXISTS integration_setup (
    provider text NOT NULL,
    guide_version integer NOT NULL,
    step integer NOT NULL CHECK (step >= 1),
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, guide_version, step)
);
CREATE TABLE IF NOT EXISTS integration_activity (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider text NOT NULL,
    actor text NOT NULL DEFAULT 'owner',
    operation text NOT NULL,
    status text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
