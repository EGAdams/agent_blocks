-- How a task came to belong to a request.
--
-- Until now LinkRequestPhase guessed: every task a client submitted joined that
-- client's newest open request covering the task's agent. On 2026-11-17 a request
-- to fix Notes PDF printing had thirteen tasks hanging off it, most of them other
-- work entirely, and the failure of one about Meijer receipts was reported as that
-- request's blocker. A caller can now name the request its task belongs to, and
-- this column records which of the two happened:
--
--   declared  the submitter named the request (request_id on the task envelope)
--   adopted   nobody named one; the gateway attached it to the newest open request
--
-- Existing rows predate the choice, so they are adopted: that is what they were.
-- Additive only.

ALTER TABLE request_tasks ADD COLUMN origin TEXT NOT NULL DEFAULT 'adopted';
