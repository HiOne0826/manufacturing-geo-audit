CREATE TABLE IF NOT EXISTS projects (
    id BIGSERIAL PRIMARY KEY,
    client_name TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    company_name TEXT DEFAULT '',
    product_category TEXT DEFAULT '',
    target_region TEXT DEFAULT '',
    website_domain TEXT DEFAULT '',
    competitors TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS questions (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    industry TEXT DEFAULT '',
    product_category TEXT DEFAULT '',
    question_type TEXT DEFAULT '',
    question TEXT NOT NULL,
    question_source TEXT DEFAULT '',
    product_line TEXT DEFAULT '',
    purchase_stage TEXT DEFAULT '',
    scenario TEXT DEFAULT '',
    suggested_platforms TEXT DEFAULT '',
    optimization_goal TEXT DEFAULT '',
    top30_pushed TEXT DEFAULT '',
    first_screen_order INTEGER DEFAULT 0,
    filter_reason TEXT DEFAULT '',
    target_brand TEXT DEFAULT '',
    competitor_brands TEXT DEFAULT '',
    locale TEXT DEFAULT 'zh-CN',
    priority TEXT DEFAULT 'medium',
    notes TEXT DEFAULT '',
    import_row_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_configs (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    api_family TEXT DEFAULT '',
    model TEXT NOT NULL,
    model_version TEXT DEFAULT '',
    model_type TEXT DEFAULT 'chat',
    api_key TEXT DEFAULT '',
    api_base TEXT DEFAULT '',
    priority INTEGER DEFAULT 100,
    daily_limit INTEGER DEFAULT 0,
    supports_pure INTEGER DEFAULT 1,
    supports_search INTEGER DEFAULT 0,
    web_search_mode TEXT DEFAULT '',
    web_search_param_path TEXT DEFAULT '',
    supports_reasoning INTEGER DEFAULT 0,
    reasoning_param_path TEXT DEFAULT '',
    reasoning_levels TEXT DEFAULT '',
    supports_citation INTEGER DEFAULT 0,
    citation_param_path TEXT DEFAULT '',
    supports_site_filter INTEGER DEFAULT 0,
    supports_time_filter INTEGER DEFAULT 0,
    supports_user_location INTEGER DEFAULT 0,
    supports_tool_calling INTEGER DEFAULT 1,
    active INTEGER DEFAULT 1,
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sampling_batches (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL UNIQUE,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    total_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    completed_count INTEGER DEFAULT 0,
    config_json JSONB DEFAULT '{}'::jsonb,
    batch_name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    purpose TEXT DEFAULT '',
    tags_json JSONB DEFAULT '[]'::jsonb,
    config_snapshot_json JSONB DEFAULT '{}'::jsonb,
    client_request_id TEXT DEFAULT '',
    generation INTEGER DEFAULT 1,
    lock_version INTEGER DEFAULT 0,
    archived_at TIMESTAMPTZ,
    error_message TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    batch_id TEXT NOT NULL,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    model_config_id BIGINT DEFAULT 0,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    model_version TEXT DEFAULT '',
    search_enabled INTEGER DEFAULT 0,
    temperature DOUBLE PRECISION DEFAULT 0,
    repeat_index INTEGER DEFAULT 1,
    requested_at TIMESTAMPTZ NOT NULL,
    response_text TEXT DEFAULT '',
    citations_json JSONB DEFAULT '[]'::jsonb,
    latency_ms INTEGER DEFAULT 0,
    cost_estimate DOUBLE PRECISION DEFAULT 0,
    status TEXT NOT NULL,
    search_mode TEXT DEFAULT 'off',
    thinking_type TEXT DEFAULT 'disabled',
    reasoning_effort TEXT DEFAULT '',
    thinking_budget INTEGER,
    error_message TEXT DEFAULT '',
    raw_response_json JSONB DEFAULT '{}'::jsonb,
    is_current INTEGER DEFAULT 1,
    superseded_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sampling_tasks (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    task_key TEXT NOT NULL UNIQUE,
    batch_id TEXT NOT NULL,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    model_config_id BIGINT NOT NULL,
    repeat_index INTEGER DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt_count INTEGER DEFAULT 0,
    rq_job_id TEXT DEFAULT '',
    lease_owner TEXT DEFAULT '',
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    chat_id TEXT DEFAULT '',
    artifact_dir TEXT DEFAULT '',
    error_code TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    task_snapshot_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS answer_evaluations (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES model_runs(run_id) ON DELETE CASCADE,
    target_brand_mentioned INTEGER DEFAULT 0,
    target_brand_rank INTEGER,
    recommendation_strength TEXT DEFAULT '未提及',
    sentiment TEXT DEFAULT '中性',
    competitors_mentioned TEXT DEFAULT '',
    owned_site_cited INTEGER DEFAULT 0,
    third_party_cited INTEGER DEFAULT 0,
    factual_errors TEXT DEFAULT '',
    risk_level TEXT DEFAULT '低',
    evaluator TEXT DEFAULT 'rule',
    evaluation_notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_attempts (
    id BIGSERIAL PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    task_id TEXT DEFAULT '',
    task_key TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    run_id TEXT DEFAULT '',
    attempt_no INTEGER NOT NULL DEFAULT 1,
    configured_provider TEXT DEFAULT '',
    actual_provider TEXT DEFAULT '',
    configured_model TEXT DEFAULT '',
    actual_model TEXT DEFAULT '',
    mode TEXT DEFAULT 'pure',
    config_fingerprint TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    error_code TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    response_received INTEGER DEFAULT 0,
    persistence_committed INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    usage_json JSONB DEFAULT '{}'::jsonb,
    cost_estimate DOUBLE PRECISION DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_attempts_sequence
ON execution_attempts(batch_id, task_key, attempt_no);

CREATE TABLE IF NOT EXISTS dispatch_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json JSONB DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    last_error TEXT DEFAULT '',
    claim_token TEXT DEFAULT '',
    claim_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    queue_name TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    metadata_json JSONB DEFAULT '{}'::jsonb,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_health (
    health_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT DEFAULT '',
    mode TEXT DEFAULT 'pure',
    status TEXT NOT NULL DEFAULT 'unknown',
    consecutive_failures INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_error_code TEXT DEFAULT '',
    last_error_message TEXT DEFAULT '',
    circuit_open_until TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    checked_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provider_health_scopes (
    health_key TEXT PRIMARY KEY,
    endpoint TEXT DEFAULT '',
    credential_fingerprint TEXT DEFAULT 'unconfigured',
    exit_region TEXT DEFAULT '',
    scope_json JSONB DEFAULT '{}'::jsonb,
    half_open_trial_until TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_health_events (
    event_id TEXT PRIMARY KEY,
    health_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT DEFAULT '',
    mode TEXT DEFAULT 'pure',
    ok INTEGER NOT NULL,
    error_code TEXT DEFAULT '',
    latency_ms INTEGER DEFAULT 0,
    source TEXT DEFAULT 'passive',
    observed_at TIMESTAMPTZ NOT NULL
);

-- Existing PostgreSQL tables are not altered by CREATE TABLE IF NOT EXISTS.
-- Add every column used by the indexes below before creating those indexes.
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS model_config_id BIGINT DEFAULT 0;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS search_mode TEXT DEFAULT 'off';
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS thinking_type TEXT DEFAULT 'disabled';
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS reasoning_effort TEXT DEFAULT '';
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS thinking_budget INTEGER;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS repeat_index INTEGER DEFAULT 1;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS is_current INTEGER DEFAULT 1;
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_model_runs_batch_id ON model_runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_model_runs_project_id ON model_runs(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_runs_one_current
ON model_runs (
    batch_id, question_id, model_config_id, search_enabled,
    COALESCE(search_mode, ''), COALESCE(thinking_type, ''),
    COALESCE(reasoning_effort, ''), COALESCE(thinking_budget, -1), repeat_index
) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_sampling_batches_project_id ON sampling_batches(project_id);
CREATE INDEX IF NOT EXISTS idx_sampling_tasks_batch_status ON sampling_tasks(batch_id, status, id);
CREATE INDEX IF NOT EXISTS idx_execution_attempts_batch_task ON execution_attempts(batch_id, task_key, attempt_no);
CREATE INDEX IF NOT EXISTS idx_dispatch_outbox_pending ON dispatch_outbox(status, available_at, id);
CREATE INDEX IF NOT EXISTS idx_provider_health_events_window ON provider_health_events(health_key, observed_at);
ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS batch_name TEXT DEFAULT '';
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS purpose TEXT DEFAULT '';
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS tags_json JSONB DEFAULT '[]'::jsonb;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS config_snapshot_json JSONB DEFAULT '{}'::jsonb;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS client_request_id TEXT DEFAULT '';
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS generation INTEGER DEFAULT 1;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS lock_version INTEGER DEFAULT 0;
ALTER TABLE sampling_batches ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS idx_sampling_batches_client_request
ON sampling_batches(project_id, client_request_id) WHERE client_request_id <> '';
-- Existing V1 databases may contain multiple active batches per project.
-- Keep the most recently updated batch active and retain older rows as
-- failed_system history before enforcing the uniqueness invariant.
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
