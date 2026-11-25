CREATE TABLE IF NOT EXISTS objective_challenge (
    objective_id uuid NOT NULL REFERENCES objective_contract(id),
    version integer NOT NULL,
    level text CHECK (level IN ('light','standard','deep')),
    risk jsonb,
    requested_level text CHECK (requested_level IN ('light','standard','deep')),
    override_reason text,
    generation integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objective_id,version)
);
CREATE TABLE IF NOT EXISTS objective_challenge_artifact (
    id uuid PRIMARY KEY,
    objective_id uuid NOT NULL,
    version integer NOT NULL,
    generation integer NOT NULL,
    stage text NOT NULL,
    execution_id uuid REFERENCES objective_run(id),
    status text NOT NULL CHECK (status IN ('running','complete','failed','uncertain')),
    provider text NOT NULL,
    model text NOT NULL,
    evidence jsonb NOT NULL,
    result jsonb,
    usage jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(objective_id,version,generation,stage),
    FOREIGN KEY (objective_id,version) REFERENCES objective_challenge(objective_id,version)
);
