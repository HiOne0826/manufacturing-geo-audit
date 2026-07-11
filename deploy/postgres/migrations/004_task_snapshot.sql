ALTER TABLE sampling_tasks
ADD COLUMN IF NOT EXISTS task_snapshot_json JSONB DEFAULT '{}'::jsonb;
