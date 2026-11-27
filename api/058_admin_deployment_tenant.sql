-- The Command Center owner has a stable default tenant independent of its display name.
ALTER TABLE command_center_tenant
    ADD COLUMN IF NOT EXISTS admin_owner_id uuid UNIQUE REFERENCES smartdev_owner(id);

INSERT INTO command_center_tenant(name, admin_owner_id)
SELECT available.name, owner.id
FROM smartdev_owner owner
CROSS JOIN LATERAL (
    SELECT CASE WHEN n=1 THEN 'Admin' ELSE format('Admin (%s)', n) END AS name
    FROM generate_series(1, 1 + (SELECT count(*) FROM command_center_tenant)) n
    WHERE NOT EXISTS (
        SELECT 1 FROM command_center_tenant t
        WHERE t.name=CASE WHEN n=1 THEN 'Admin' ELSE format('Admin (%s)', n) END
    )
    ORDER BY n LIMIT 1
) available
WHERE owner.singleton AND NOT EXISTS (
    SELECT 1 FROM command_center_tenant t WHERE t.admin_owner_id=owner.id
)
ON CONFLICT (admin_owner_id) DO NOTHING;

CREATE OR REPLACE FUNCTION smd_admin_tenant_id() RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT t.id FROM command_center_tenant t JOIN smartdev_owner o ON o.id=t.admin_owner_id WHERE o.singleton
$$;

-- The approved installation target stays on smd_deploy_task. Every new deployment
-- starts under Admin. Later owner reassignments update the existing assignment row.
CREATE OR REPLACE FUNCTION smd_initial_deployment_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.tenant_id := smd_admin_tenant_id();
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER smd_initial_deployment_tenant BEFORE INSERT ON smd_deployment_assignment
    FOR EACH ROW EXECUTE FUNCTION smd_initial_deployment_tenant();

WITH filled AS (
    UPDATE smd_deployment_assignment SET tenant_id=smd_admin_tenant_id(), version=version+1
    WHERE tenant_id IS NULL RETURNING task_id, tenant_id
)
INSERT INTO smd_deployment_event(task_id,kind,label,next_tenant)
SELECT task_id,'assignment','Assigned to the default admin tenant',tenant_id FROM filled;

ALTER TABLE smd_deployment_assignment ALTER COLUMN tenant_id SET NOT NULL;

CREATE OR REPLACE FUNCTION smd_log_initial_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO smd_deployment_event(task_id,kind,label,next_tenant)
    VALUES (NEW.task_id,'assignment','Assigned to the default admin tenant',NEW.tenant_id);
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER smd_log_initial_tenant AFTER INSERT ON smd_deployment_assignment
    FOR EACH ROW EXECUTE FUNCTION smd_log_initial_tenant();
