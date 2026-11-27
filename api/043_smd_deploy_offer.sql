ALTER TABLE smd_deploy_task
    ADD COLUMN IF NOT EXISTS offer_token text,
    ADD COLUMN IF NOT EXISTS region text,
    ADD COLUMN IF NOT EXISTS plan text,
    ADD COLUMN IF NOT EXISTS os_id integer,
    ADD COLUMN IF NOT EXISTS monthly_cost_usd integer,
    ADD COLUMN IF NOT EXISTS release_revision text,
    ADD COLUMN IF NOT EXISTS installer_url text,
    ADD COLUMN IF NOT EXISTS installer_sha256 text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'smd_deploy_task'::regclass AND conname = 'smd_deploy_offer_complete'
    ) THEN
        ALTER TABLE smd_deploy_task ADD CONSTRAINT smd_deploy_offer_complete CHECK (
            num_nonnulls(offer_token, region, plan, os_id, monthly_cost_usd, release_revision,
                         installer_url, installer_sha256) IN (0, 8)
        );
    END IF;
END;
$$;

UPDATE smd_deploy_task
SET status = 'needs_attention',
    note = 'This deployment request predates release pinning and cannot be picked up.',
    updated_at = now()
WHERE status = 'queued' AND offer_token IS NULL;
