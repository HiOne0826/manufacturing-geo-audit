-- Preserve all legacy batches while enforcing the V2 one-active-batch invariant.
-- The newest updated row (then highest id as deterministic tie-breaker) remains active.
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

UPDATE sampling_batches AS stale
SET status = 'failed_system',
    error_message = CASE
        WHEN BTRIM(COALESCE(stale.error_message, '')) = ''
            THEN 'V2 migration: superseded duplicate active batch'
        ELSE stale.error_message || E'\n' || 'V2 migration: superseded duplicate active batch'
    END,
    finished_at = COALESCE(stale.finished_at, CURRENT_TIMESTAMP),
    updated_at = CURRENT_TIMESTAMP
WHERE stale.status IN ('queued', 'running', 'pause_requested', 'paused')
  AND stale.archived_at IS NULL
  AND EXISTS (
      SELECT 1
      FROM sampling_batches AS newer
      WHERE newer.project_id = stale.project_id
        AND newer.status IN ('queued', 'running', 'pause_requested', 'paused')
        AND newer.archived_at IS NULL
        AND (
            COALESCE(newer.updated_at, '-infinity'::timestamptz)
                > COALESCE(stale.updated_at, '-infinity'::timestamptz)
            OR (
                COALESCE(newer.updated_at, '-infinity'::timestamptz)
                    = COALESCE(stale.updated_at, '-infinity'::timestamptz)
                AND newer.id > stale.id
            )
        )
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_sampling_batches_one_active_project
ON sampling_batches(project_id)
WHERE status IN ('queued', 'running', 'pause_requested', 'paused') AND archived_at IS NULL;
