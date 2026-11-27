ALTER TABLE objective_contract ADD COLUMN IF NOT EXISTS business_objective text;

UPDATE objective_contract
SET business_objective='BO-01 — Deploy SMD'
WHERE id='10b332ba-ec84-43b8-8323-02f61b0602e1'
  AND business_objective IS NULL;
