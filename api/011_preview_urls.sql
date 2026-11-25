CREATE TABLE IF NOT EXISTS project_preview_url (
    project_id uuid NOT NULL REFERENCES projects(id),
    environment text NOT NULL CHECK(environment IN ('DEV','QA','PREPROD','PROD')),
    mode text NOT NULL CHECK(mode IN ('url','automatic')),
    url text,
    PRIMARY KEY(project_id,environment)
);
