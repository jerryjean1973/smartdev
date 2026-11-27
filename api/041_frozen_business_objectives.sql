CREATE TABLE IF NOT EXISTS business_objective (
    id text PRIMARY KEY,
    title text NOT NULL,
    expected_outcome text NOT NULL,
    priority text NOT NULL CHECK (priority IN ('P0','P1','P2')),
    execution_order integer NOT NULL UNIQUE,
    current_focus boolean NOT NULL DEFAULT false
);

INSERT INTO business_objective (id,title,expected_outcome,priority,execution_order,current_focus) SELECT * FROM (VALUES
('BO-01','Multi-environment deployment','Build the capability to deploy complete, independent SMD environments on your Vultr infrastructure. Azure/AWS are later options.','P0',1,true),
('BO-02','Tenant and deployment management','Owner-only Command Center to manage tenants, projects, Coder workspaces, hosts and deployments.','P0',2,false),
('BO-03','Native/custom code isolation','Separate SmartDev platform code from customer applications so platform upgrades never overwrite customer work.','P1',3,false),
('BO-06','SMD default environment','Standard SMD environment with UI, Vault, Architect SLM, BRIPe Governance and SRF; optional local MIG and full BRIPe.','P1',4,false),
('BO-04','Kanban in left sidebar','Seven-stage project workflow: Requested → Queued → Reviewed → Processed → Tested → Requeued → Completed.','P1',5,false),
('BO-05','Multi-agent and multithreading options','Configurable concurrent agents and tasks, with isolated execution and coordinated results.','P1',6,false),
('BO-07','Idea-to-app','Turn an idea into a working application through architecture, coding, testing, repair and deployment.','P2',7,false),
('BO-08','Automatic reusable skills','Detect recurring workflows, generate reusable skills, register them and reuse them across authorized tasks.','P2',8,false)
) AS seed(id,title,expected_outcome,priority,execution_order,current_focus)
WHERE current_setting('smartdev.install_kind',true) IS DISTINCT FROM 'tenant'
ON CONFLICT (id) DO NOTHING;

UPDATE objective_contract AS task
SET business_objective=assignment.business_objective
FROM (VALUES
    ('10b332ba-ec84-43b8-8323-02f61b0602e1'::uuid,'BO-01'),
    ('8a49b8c2-dfa2-40b9-979d-0a387c2f74da'::uuid,'BO-01'),
    ('c0b3d846-6370-4a3e-86c3-444d2e5c578e'::uuid,'BO-01'),
    ('3be26260-833a-420f-bfe6-6dce23638e81'::uuid,'BO-01'),
    ('63f0645b-2ee1-4047-a135-e7b1ae7a81b9'::uuid,'BO-02'),
    ('9ca7a005-9ba5-40fb-b71c-47ea890e6c9c'::uuid,'BO-02'),
    ('2681fe53-d45a-4c35-8773-7a4bca71d951'::uuid,'BO-04'),
    ('1e590d7a-a185-406a-81dd-0a1b32473a68'::uuid,'BO-04'),
    ('41d741e6-7f14-4c37-adea-06a082d76eb3'::uuid,'BO-04'),
    ('35030006-87cc-4c31-9ca4-606f9bcc6cb6'::uuid,'BO-05'),
    ('46471d3e-c28d-4caf-a7ad-115efa2a9b33'::uuid,'BO-05'),
    ('bd745808-eb75-4d8c-befb-8c4e14f27b6a'::uuid,'BO-07'),
    ('922a92b7-551b-482d-863d-1ce6a76c3382'::uuid,'BO-07')
) AS assignment(task_id,business_objective)
WHERE task.id=assignment.task_id
  AND (task.business_objective IS NULL OR task.business_objective='BO-01 — Deploy SMD');
