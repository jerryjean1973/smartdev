CREATE TABLE IF NOT EXISTS taskboard_platform_project (
    project_id uuid PRIMARY KEY REFERENCES projects(id)
);
INSERT INTO taskboard_platform_project(project_id)
SELECT id FROM projects WHERE id='bca501cf-7f58-4cd6-b254-33bf4fe48c7c'
  AND current_setting('smartdev.install_kind',true) IS DISTINCT FROM 'tenant'
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS taskboard_item (
    number bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id), item_id uuid NOT NULL,
    UNIQUE(project_id,item_id)
);
CREATE OR REPLACE FUNCTION taskboard_number(n bigint) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT 'CC-'||lpad(n::text,greatest(6,length(n::text)),'0')
$$;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS board_item uuid;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS ingress_actor uuid;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS ingress_client text;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS ingress_tenant text;
ALTER TABLE mcp_task_request ADD COLUMN IF NOT EXISTS failure_reason text;

CREATE TABLE IF NOT EXISTS taskboard_repair (
    project_id uuid NOT NULL, request_id uuid NOT NULL, revision text NOT NULL CHECK(revision ~ '^[0-9a-f]{40}$'),
    summary text NOT NULL, evidence text[] NOT NULL, verified_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(project_id,request_id,revision),
    FOREIGN KEY(project_id,request_id) REFERENCES mcp_task_request(project_id,request_id)
);
-- A follow-up can use an existing Codex conversation as its delivery address.
-- Only an admission match binds a distinct instruction to earlier work.
WITH RECURSIVE roots AS (
  SELECT r.project_id,r.request_id,CASE WHEN r.kind='create' THEN coalesce(r.task_id,r.request_id) ELSE r.request_id END AS item_id
  FROM mcp_task_request r WHERE r.matched_request IS NULL
  UNION ALL SELECT r.project_id,r.request_id,roots.item_id FROM mcp_task_request r
    JOIN roots ON (roots.project_id,roots.request_id)=(r.project_id,r.matched_request)
) UPDATE mcp_task_request r SET board_item=roots.item_id FROM roots
  WHERE (r.project_id,r.request_id)=(roots.project_id,roots.request_id) AND r.board_item IS NULL;
UPDATE mcp_task_request SET board_item=coalesce(matched_request,request_id) WHERE board_item IS NULL;

WITH items AS (
  SELECT project_id,id AS item_id,created_at AS opened FROM objective_contract
  UNION ALL SELECT project_id,board_item,received_at FROM mcp_task_request
), missing AS (
  SELECT i.project_id,i.item_id,min(i.opened) AS opened FROM items i
  WHERE NOT EXISTS (SELECT 1 FROM taskboard_item p WHERE (p.project_id,p.item_id)=(i.project_id,i.item_id))
  GROUP BY i.project_id,i.item_id
) INSERT INTO taskboard_item(project_id,item_id) SELECT project_id,item_id FROM missing
ORDER BY opened NULLS LAST,project_id,item_id ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION taskboard_register_item() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item uuid;
BEGIN
 IF TG_TABLE_NAME='objective_contract' THEN item:=NEW.id;
 ELSE
   IF TG_OP='UPDATE' AND OLD.board_item IS NOT NULL THEN NEW.board_item:=OLD.board_item; END IF;
   IF NEW.board_item IS NULL THEN
     SELECT board_item INTO item FROM mcp_task_request WHERE project_id=NEW.project_id AND request_id=NEW.matched_request;
     NEW.board_item:=coalesce(item,CASE WHEN NEW.kind='create' THEN NEW.task_id END,NEW.request_id);
   END IF;
   item:=NEW.board_item;
 END IF;
 INSERT INTO taskboard_item(project_id,item_id) VALUES(NEW.project_id,item) ON CONFLICT DO NOTHING;
 RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER taskboard_objective_number BEFORE INSERT ON objective_contract FOR EACH ROW EXECUTE FUNCTION taskboard_register_item();
CREATE OR REPLACE TRIGGER taskboard_request_number BEFORE INSERT OR UPDATE OF task_id,admission ON mcp_task_request FOR EACH ROW EXECUTE FUNCTION taskboard_register_item();

CREATE OR REPLACE FUNCTION cc_board_destination(project uuid, scope_id text, objective_scope text) RETURNS boolean
LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT 1 FROM taskboard_platform_project p WHERE p.project_id=project)
 OR EXISTS(SELECT 1 FROM business_objective b WHERE b.id=scope_id AND b.id=substring(objective_scope from '^BO-[0-9]+'))
$$;

ALTER TABLE taskboard_item ADD COLUMN IF NOT EXISTS description text;
UPDATE taskboard_item n SET description=review.description FROM mcp_task_request r
JOIN (VALUES
 ('2a594cde0a8adf333f05b7b5','Show all Taskboard sections in a sortable table with permanent task numbers'),
 ('db435289c5ba643b9074358f','Keep workspace-only conversations off the Command Center board and show request provenance'),
 ('0d96120edccc37d1c6d47395','Make every Kanban column reachable on desktop and mobile'),
 ('4d86bd3f27f301b2667a3ed4','Combine source and task filters and show failed executions')
) review(receipt,description) ON review.receipt=r.execution_ref
WHERE (n.project_id,n.item_id)=(r.project_id,r.board_item)
  AND r.project_id='bca501cf-7f58-4cd6-b254-33bf4fe48c7c' AND n.description IS NULL;
