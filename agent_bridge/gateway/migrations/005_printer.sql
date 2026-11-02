-- Printer tasks are ordinary tasks (agent 'printer'); PrintGuard and
-- SqlitePrinterJobs look them up per client and operation, newest first.
-- Additive only: no table or row is changed.
CREATE INDEX IF NOT EXISTS tasks_printer ON tasks (client_id, operation, created_at);
