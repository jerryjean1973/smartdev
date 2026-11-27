CREATE TABLE IF NOT EXISTS objective_owner_approval (
    objective_id uuid NOT NULL,
    version integer NOT NULL,
    run_id uuid REFERENCES objective_run(id),
    approved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objective_id, version),
    FOREIGN KEY (objective_id, version) REFERENCES objective_revision(objective_id, version)
);
