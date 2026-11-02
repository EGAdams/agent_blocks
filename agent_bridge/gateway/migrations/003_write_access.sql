-- Scoped write access: structured result details, and where each agent may write.
-- Additive only: existing rows get an empty details object and no write workspaces.

ALTER TABLE task_results ADD COLUMN details TEXT NOT NULL DEFAULT '{}';
ALTER TABLE agent_registry ADD COLUMN write_workspaces TEXT NOT NULL DEFAULT '[]';
