ALTER TABLE objective_contract ADD COLUMN IF NOT EXISTS sidebar_task boolean NOT NULL DEFAULT false;

-- A dedicated conversation whose ID is the objective ID is created by explicit
-- task admission. Shared and legacy conversations do not establish that origin.
UPDATE objective_contract task
SET sidebar_task = true
WHERE NOT task.sidebar_task
  AND EXISTS (
    SELECT 1 FROM project_conversation chat
    WHERE chat.project_id = task.project_id
      AND chat.id = task.id AND chat.active_task_id = task.id AND NOT chat.legacy
  )
  AND NOT EXISTS (
    SELECT 1 FROM objective_presentation hidden
    WHERE hidden.objective_id = task.id AND hidden.excluded_from_board
  );
