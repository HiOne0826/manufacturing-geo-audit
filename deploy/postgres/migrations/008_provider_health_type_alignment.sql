-- Align provider-health tables that may have been created by early runtime DDL.
ALTER TABLE provider_health_scopes
    ALTER COLUMN scope_json TYPE JSONB USING scope_json::jsonb;
ALTER TABLE provider_health_scopes
    ALTER COLUMN half_open_trial_until TYPE TIMESTAMPTZ
    USING NULLIF(half_open_trial_until::text, '')::timestamptz;
ALTER TABLE provider_health_scopes
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ
    USING updated_at::text::timestamptz;
ALTER TABLE provider_health_events
    ALTER COLUMN observed_at TYPE TIMESTAMPTZ
    USING observed_at::text::timestamptz;
