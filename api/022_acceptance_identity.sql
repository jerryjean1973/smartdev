ALTER TABLE conversation_submission ADD COLUMN IF NOT EXISTS environment text
    CHECK (environment IN ('user', 'acceptance'));
