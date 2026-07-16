-- Bring pre-V2 PostgreSQL tables up to the columns assumed by current code.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE questions ADD COLUMN IF NOT EXISTS question_source TEXT DEFAULT '';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS product_line TEXT DEFAULT '';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS purchase_stage TEXT DEFAULT '';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS scenario TEXT DEFAULT '';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS suggested_platforms TEXT DEFAULT '';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS optimization_goal TEXT DEFAULT '';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS top30_pushed TEXT DEFAULT '';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS first_screen_order INTEGER DEFAULT 0;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS filter_reason TEXT DEFAULT '';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS import_row_json JSONB DEFAULT '{}'::jsonb;

ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS web_search_mode TEXT DEFAULT '';
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS web_search_param_path TEXT DEFAULT '';
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS supports_reasoning INTEGER DEFAULT 0;
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS reasoning_param_path TEXT DEFAULT '';
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS reasoning_levels TEXT DEFAULT '';
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS supports_citation INTEGER DEFAULT 0;
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS citation_param_path TEXT DEFAULT '';
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS supports_site_filter INTEGER DEFAULT 0;
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS supports_time_filter INTEGER DEFAULT 0;
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS supports_user_location INTEGER DEFAULT 0;
ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS supports_tool_calling INTEGER DEFAULT 1;

ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS model_config_id BIGINT DEFAULT 0;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS search_mode TEXT DEFAULT 'off';
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS thinking_type TEXT DEFAULT 'disabled';
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS reasoning_effort TEXT DEFAULT '';
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS thinking_budget INTEGER;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS raw_response_json JSONB DEFAULT '{}'::jsonb;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS repeat_index INTEGER DEFAULT 1;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS is_current INTEGER DEFAULT 1;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS batch_name TEXT DEFAULT '';
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS purpose TEXT DEFAULT '';
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS tags_json JSONB DEFAULT '[]'::jsonb;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS config_snapshot_json JSONB DEFAULT '{}'::jsonb;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS client_request_id TEXT DEFAULT '';
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS generation INTEGER DEFAULT 1;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS lock_version INTEGER DEFAULT 0;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE sampling_tasks ADD COLUMN IF NOT EXISTS lease_owner TEXT DEFAULT '';
ALTER TABLE sampling_tasks ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE sampling_tasks ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE sampling_tasks ADD COLUMN IF NOT EXISTS chat_id TEXT DEFAULT '';
ALTER TABLE sampling_tasks ADD COLUMN IF NOT EXISTS artifact_dir TEXT DEFAULT '';
ALTER TABLE sampling_tasks ADD COLUMN IF NOT EXISTS error_code TEXT DEFAULT '';
ALTER TABLE sampling_tasks ADD COLUMN IF NOT EXISTS task_snapshot_json JSONB DEFAULT '{}'::jsonb;

ALTER TABLE dispatch_outbox ADD COLUMN IF NOT EXISTS claim_token TEXT DEFAULT '';
ALTER TABLE dispatch_outbox ADD COLUMN IF NOT EXISTS claim_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_model_runs_batch_id ON model_runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_model_runs_project_id ON model_runs(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_runs_one_current
ON model_runs (
    batch_id, question_id, model_config_id, search_enabled,
    COALESCE(search_mode, ''), COALESCE(thinking_type, ''),
    COALESCE(reasoning_effort, ''), COALESCE(thinking_budget, -1), repeat_index
) WHERE is_current = 1;
