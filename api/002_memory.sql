CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS memory (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    scope text NOT NULL CHECK (scope IN ('project','agent','shared')),
    title text NOT NULL,
    body text NOT NULL,
    provenance text NOT NULL,
    confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    state text NOT NULL CHECK (state IN ('proposed','implemented','tested','verified','failed','reverted','invalidated')),
    agent_id uuid,
    supersedes uuid UNIQUE REFERENCES memory(id),
    fingerprint text NOT NULL UNIQUE,
    embedding vector(1536),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (scope <> 'agent' OR agent_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS memory_project_idx ON memory(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS memory_keyword_idx ON memory USING gin
    (to_tsvector('simple', title || ' ' || body || ' ' || provenance));
