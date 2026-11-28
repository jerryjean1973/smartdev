ALTER TABLE integration_ai_role DROP CONSTRAINT IF EXISTS integration_ai_role_provider_check;
ALTER TABLE integration_ai_role DROP CONSTRAINT IF EXISTS integration_ai_role_check;
ALTER TABLE integration_ai_role DROP CONSTRAINT IF EXISTS integration_ai_role_assignment_check;

ALTER TABLE integration_ai_role
    ADD CONSTRAINT integration_ai_role_provider_check
    CHECK (provider IN ('openai','anthropic','codex_cli','claude_code'));

ALTER TABLE integration_ai_role
    ADD CONSTRAINT integration_ai_role_assignment_check
    CHECK ((role='coder' AND provider IN ('codex_cli','claude_code') AND model IS NULL) OR
           (role<>'coder' AND provider NOT IN ('codex_cli','claude_code') AND model IS NOT NULL));
