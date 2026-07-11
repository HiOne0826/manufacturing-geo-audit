ALTER TABLE dispatch_outbox ADD COLUMN IF NOT EXISTS claim_token TEXT DEFAULT '';
ALTER TABLE dispatch_outbox ADD COLUMN IF NOT EXISTS claim_expires_at TIMESTAMPTZ;

WITH numbered AS (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY batch_id, task_key ORDER BY attempt_no, id) AS next_no
    FROM execution_attempts
)
UPDATE execution_attempts AS attempt
SET attempt_no = numbered.next_no
FROM numbered
WHERE attempt.id = numbered.id AND attempt.attempt_no <> numbered.next_no;

CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_attempts_sequence
ON execution_attempts(batch_id, task_key, attempt_no);
