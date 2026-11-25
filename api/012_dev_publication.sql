CREATE TABLE IF NOT EXISTS project_dev_publication (
    project_id uuid PRIMARY KEY REFERENCES projects(id),
    enabled boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 0
);
