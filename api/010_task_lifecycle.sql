ALTER TABLE objective_contract DROP CONSTRAINT IF EXISTS objective_contract_status_check;
ALTER TABLE objective_contract ALTER COLUMN status SET DEFAULT 'active';
UPDATE objective_contract SET status=CASE WHEN status IN ('BLOCKED','FAILED') THEN 'blocked' ELSE 'active' END
WHERE status IN ('PENDING','RUNNING','BLOCKED','FAILED','COMPLETED');
ALTER TABLE objective_contract ADD CONSTRAINT objective_contract_status_check
CHECK (status IN ('active','blocked','ready_for_review','done'));

INSERT INTO objective_presentation(objective_id,placement,reason)
SELECT c.id,'conversation','Legacy automatic browser routing created an objective and identical criterion for a status question.'
FROM objective_contract c JOIN projects p ON p.id=c.project_id
WHERE c.id='82044ed2-0c11-49fd-9e2b-0786093ccec4' AND p.name='BC' AND lower(trim(c.original_request))='is it working'
  AND jsonb_array_length(c.criteria)=1 AND lower(trim(c.criteria->0->>'description'))='is it working'
ON CONFLICT DO NOTHING;
