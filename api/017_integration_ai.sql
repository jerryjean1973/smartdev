CREATE TABLE IF NOT EXISTS integration_ai_role (
    scope text NOT NULL,
    role text NOT NULL CHECK (role IN ('prompter','challenger','coder','reviewer')),
    provider text NOT NULL CHECK (provider IN ('openai','anthropic','codex_cli')),
    model text,
    PRIMARY KEY (scope,role),
    CHECK ((role='coder' AND provider='codex_cli' AND model IS NULL) OR
           (role<>'coder' AND provider<>'codex_cli' AND model IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS integration_ai_check (
    id uuid PRIMARY KEY,
    project_id uuid REFERENCES projects(id),
    provider text NOT NULL,
    model text NOT NULL,
    status text NOT NULL,
    usage jsonb,
    checked_at timestamptz NOT NULL DEFAULT now()
);
