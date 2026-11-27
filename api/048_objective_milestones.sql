CREATE TABLE IF NOT EXISTS objective_milestone (
    objective_id uuid NOT NULL REFERENCES objective_contract(id) ON DELETE CASCADE,
    position smallint NOT NULL CHECK (position > 0),
    title text NOT NULL,
    status text NOT NULL CHECK (status IN ('done', 'working', 'blocked', 'pending')),
    detail text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objective_id, position)
);
