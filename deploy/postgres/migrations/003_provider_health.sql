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

CREATE INDEX IF NOT EXISTS idx_provider_health_events_window
ON provider_health_events(health_key, observed_at);
