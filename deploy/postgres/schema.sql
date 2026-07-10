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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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

CREATE INDEX IF NOT EXISTS idx_model_runs_batch_id ON model_runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_model_runs_project_id ON model_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_sampling_batches_project_id ON sampling_batches(project_id);
CREATE INDEX IF NOT EXISTS idx_sampling_tasks_batch_status ON sampling_tasks(batch_id, status, id);
