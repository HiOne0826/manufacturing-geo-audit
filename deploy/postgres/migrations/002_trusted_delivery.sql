-- Additive P1 trusted-delivery schema. Safe to apply repeatedly.
-- Keep this migration independent from the application bootstrap so deployments
-- can run it before rolling out API handlers.

CREATE TABLE IF NOT EXISTS run_review_events (
    id BIGSERIAL PRIMARY KEY,
    review_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK(decision IN ('valid', 'excluded', 'needs_review')),
    reason TEXT DEFAULT '', actor TEXT NOT NULL,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_review_events_run ON run_review_events(run_id, id DESC);

CREATE TABLE IF NOT EXISTS report_versions (
    id BIGSERIAL PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL DEFAULT '', version_no INTEGER NOT NULL, title TEXT DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('draft', 'reviewed', 'frozen', 'archived')),
    summary_snapshot_json JSONB DEFAULT '{}'::jsonb,
    run_ids_json JSONB DEFAULT '[]'::jsonb,
    config_snapshot_json JSONB DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL, reviewed_by TEXT DEFAULT '', reviewed_at TIMESTAMPTZ,
    frozen_by TEXT DEFAULT '', frozen_at TIMESTAMPTZ,
    archived_by TEXT DEFAULT '', archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(project_id, batch_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_report_versions_project ON report_versions(project_id, batch_id, version_no DESC);

CREATE TABLE IF NOT EXISTS operation_audit_log (
    id BIGSERIAL PRIMARY KEY,
    audit_id TEXT NOT NULL UNIQUE, project_id BIGINT, batch_id TEXT DEFAULT '',
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL,
    actor TEXT NOT NULL, details_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_audit_project ON operation_audit_log(project_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_operation_audit_entity ON operation_audit_log(entity_type, entity_id, id DESC);
