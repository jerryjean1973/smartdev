-- The public installation identity stays usable for public domain discovery. Access
-- to owner integrations is irrevocably bound to its original assignment version.
CREATE TABLE IF NOT EXISTS smd_install_identity (
    task_id uuid PRIMARY KEY REFERENCES smd_deploy_task(id) ON DELETE CASCADE,
    instance_id uuid NOT NULL, public_key bytea NOT NULL CHECK (octet_length(public_key)=32),
    owner_version integer, enrolled_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS smd_config_nonce (
    task_id uuid NOT NULL REFERENCES smd_install_identity(task_id) ON DELETE CASCADE,
    nonce uuid NOT NULL, used_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(task_id,nonce)
);
CREATE INDEX IF NOT EXISTS smd_config_nonce_expiry ON smd_config_nonce(used_at);
CREATE OR REPLACE FUNCTION smd_revoke_owner_config() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id THEN
        UPDATE smd_install_identity SET owner_version=NULL WHERE task_id=NEW.task_id AND owner_version IS NOT NULL;
        IF FOUND THEN
            INSERT INTO smd_deployment_event(task_id,kind,label) VALUES(NEW.task_id,'configuration','Owner integration access revoked');
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER smd_revoke_owner_assignment AFTER UPDATE OF tenant_id ON smd_deployment_assignment
FOR EACH ROW EXECUTE FUNCTION smd_revoke_owner_config();

CREATE TABLE IF NOT EXISTS smd_custom_preview (
    task_id uuid NOT NULL REFERENCES smd_deploy_task(id) ON DELETE CASCADE,
    assignment_version integer NOT NULL, project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    environment text NOT NULL CHECK(environment IN ('DEV','QA','PREPROD','PROD')),
    hostname text NOT NULL, verified_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(task_id,assignment_version,project_id,environment)
);

CREATE TABLE IF NOT EXISTS smd_tenant_default_secret (
    tenant_id uuid NOT NULL REFERENCES command_center_tenant(id) ON DELETE CASCADE,
    environment text NOT NULL CHECK(environment IN ('DEV','QA','PREPROD','PROD')),
    name text NOT NULL,reference text NOT NULL UNIQUE,version integer NOT NULL,
    PRIMARY KEY(tenant_id,environment,name)
);
CREATE TABLE IF NOT EXISTS smd_preview_challenge (
    task_id uuid NOT NULL REFERENCES smd_deploy_task(id) ON DELETE CASCADE,
    assignment_version integer NOT NULL,project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    hostname text NOT NULL,challenge text NOT NULL,PRIMARY KEY(task_id,assignment_version,project_id,hostname)
);
