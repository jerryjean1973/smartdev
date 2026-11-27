-- The task is the delivery conversation. Each request has its own work scope.
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS request_scope text;

CREATE OR REPLACE FUNCTION mcp_inferred_scope(subject text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN subject ~* '^\s*(global platform|platform-wide|project-wide)\y'
      THEN 'global'
    WHEN subject ~* '^\s*(BO-[0-9]+)\y'
      THEN upper(substring(trim(subject) from '(?i)^(BO-[0-9]+)'))
    WHEN subject ~* '^\s*(execute|fix the actual|owner final|complete)\s+BO-[0-9]+\y'
      THEN upper(substring(trim(subject) from '(?i)(BO-[0-9]+)'))
    WHEN left(subject,180) ~* '(Ask CC|Taskboard|MCP receipt|execution receipt|global platform|platform-wide|project-wide governance)'
      THEN 'global'
    ELSE 'unassigned'
  END
$$;

-- Owner's SmartDev project. Reviewed against the requested work in each receipt.
UPDATE mcp_task_request r SET request_scope=review.scope
FROM (VALUES
 ('bfb1c34b5ccd4603d6992c92','BO-01'), ('9564961af23336aca4e29056','BO-01'),
 ('50b4a01a13896ec2a25fc9e8','BO-01'), ('3305467e48d92efc6f8d34b2','BO-02'),
 ('4c90fee39e2b0bc77d3cf290','BO-02'), ('bc29aa272dfbe4c3477e652d','BO-02'),
 ('f1d4f82052fba3351720d9fd','global'), ('451cc5ae4bdd71d5c1d67b0d','global'),
 ('57bd7651f2f2d4bbd4ea4a24','global'), ('015d0beb03d3ea7cfe076294','global'),
 ('9933a4756e32c11b5d790c2a','global')
) AS review(receipt,scope)
WHERE r.project_id='bca501cf-7f58-4cd6-b254-33bf4fe48c7c'
  AND r.execution_ref=review.receipt AND r.request_scope IS NULL;
