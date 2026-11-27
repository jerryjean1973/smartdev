CREATE TABLE IF NOT EXISTS command_center_tenant (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE CHECK (name = btrim(name) AND length(name) BETWEEN 1 AND 80)
);
INSERT INTO command_center_tenant (name)
SELECT tenant_name FROM command_center_project_tenant
UNION SELECT target_tenant FROM smd_deploy_task WHERE nullif(target_tenant,'') IS NOT NULL
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS smd_deployment_assignment (
    task_id uuid PRIMARY KEY REFERENCES smd_deploy_task(id) ON DELETE CASCADE,
    tenant_id uuid REFERENCES command_center_tenant(id), version integer NOT NULL DEFAULT 0
);
INSERT INTO smd_deployment_assignment (task_id,tenant_id)
SELECT t.id,c.id FROM smd_deploy_task t LEFT JOIN command_center_tenant c ON t.target_tenant=c.name
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS smd_deployment_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES smd_deploy_task(id) ON DELETE CASCADE,
    at timestamptz NOT NULL DEFAULT now(), kind text NOT NULL, label text NOT NULL,
    previous_tenant uuid REFERENCES command_center_tenant(id), next_tenant uuid REFERENCES command_center_tenant(id)
);
CREATE INDEX IF NOT EXISTS smd_deployment_event_task ON smd_deployment_event(task_id,at,id);

CREATE OR REPLACE FUNCTION smd_register_project_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO command_center_tenant(name) VALUES (NEW.tenant_name) ON CONFLICT DO NOTHING;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER smd_project_tenant_catalog AFTER INSERT OR UPDATE OF tenant_name ON command_center_project_tenant
FOR EACH ROW EXECUTE FUNCTION smd_register_project_tenant();

CREATE OR REPLACE FUNCTION smd_record_compute_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE tenant uuid;
BEGIN
    IF TG_OP='INSERT' THEN
        IF nullif(NEW.target_tenant,'') IS NOT NULL THEN
            INSERT INTO command_center_tenant(name) VALUES (NEW.target_tenant) ON CONFLICT DO NOTHING;
            SELECT id INTO tenant FROM command_center_tenant WHERE name=NEW.target_tenant;
        END IF;
        INSERT INTO smd_deployment_assignment(task_id,tenant_id) VALUES(NEW.id,tenant) ON CONFLICT DO NOTHING;
    ELSIF (OLD.status,OLD.note,OLD.reconciled_at) IS DISTINCT FROM (NEW.status,NEW.note,NEW.reconciled_at) THEN
        INSERT INTO smd_deployment_event(task_id,at,kind,label)
        VALUES(NEW.id,COALESCE(NEW.reconciled_at,NEW.updated_at),'state',
               CASE WHEN NEW.reconciled_at IS NOT NULL THEN 'Reconciled'
                    ELSE concat_ws(' · ',replace(NEW.status,'_',' '),nullif(NEW.note,'')) END);
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER smd_compute_changes AFTER INSERT OR UPDATE ON smd_deploy_task
FOR EACH ROW EXECUTE FUNCTION smd_record_compute_event();
