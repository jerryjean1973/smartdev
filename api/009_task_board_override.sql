ALTER TABLE objective_presentation
ADD COLUMN IF NOT EXISTS excluded_from_board boolean NOT NULL DEFAULT true;
