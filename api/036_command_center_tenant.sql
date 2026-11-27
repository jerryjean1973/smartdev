CREATE TABLE IF NOT EXISTS command_center_project_tenant (
    project_id uuid PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    tenant_name text NOT NULL CHECK (tenant_name = btrim(tenant_name) AND length(tenant_name) BETWEEN 1 AND 80)
);
