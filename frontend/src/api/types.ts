export type Project = {
  id: number;
  client_name: string;
  brand_name: string;
  company_name?: string;
  product_category?: string;
  target_region?: string;
  website_domain?: string;
  competitors?: string;
  notes?: string;
  created_at?: string;
};

export type Question = {
  id: number;
  project_id: number;
  question_id?: string;
  question: string;
  question_type: string;
  product_category?: string;
  product_line?: string;
  purchase_stage?: string;
  scenario?: string;
  priority?: string;
  suggested_platforms?: string;
  notes?: string;
  top30_pushed?: string;
  first_screen_order?: number;
  filter_reason?: string;
};

export type ModelConfig = {
  id: number;
  provider: string;
  label: string;
  model: string;
  test_platform?: string;
  model_version?: string;
  api_family?: string;
  api_base?: string;
  model_type?: string;
  priority?: number;
  daily_limit?: number;
  has_key?: boolean;
  api_key?: string;
  api_key_masked?: string;
  active?: boolean;
  supports_pure?: boolean;
  supports_search?: boolean;
  supports_reasoning?: boolean;
  supports_citation?: boolean;
  supports_site_filter?: boolean;
  supports_time_filter?: boolean;
  supports_user_location?: boolean;
  supports_tool_calling?: boolean;
  reasoning_levels?: string;
  web_search_mode?: string;
  web_search_param_path?: string;
  reasoning_param_path?: string;
  citation_param_path?: string;
  sampling_defaults?: Record<string, unknown>;
  notes?: string;
};

export type BraveSearchConfig = {
  configured: boolean;
  api_key_masked?: string;
  env_keys: string[];
  web_search_param_path: string;
  used_by: string[];
};

export type SamplingBatch = {
  batch_id: string;
  project_id: number;
  status: string;
  total_count?: number;
  completed_count?: number;
  success_count?: number;
  failed_count?: number;
  total?: number;
  completed?: number;
  success?: number;
  failed?: number;
  error?: string;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  updated_at?: string;
  task_queue_backend?: string;
  job_id?: string;
  source_statuses?: SourceRunStatus[];
};

export type SourceRunStatus = {
  test_platform: string;
  provider?: string;
  model?: string;
  total: number;
  completed: number;
  success: number;
  failed: number;
  queued: number;
  running: number;
  status: string;
  avg_latency_ms: number;
  last_error?: string;
};

export type ModelRun = {
  id: number;
  run_id?: string;
  batch_id?: string;
  source_question_id?: string;
  provider?: string;
  model?: string;
  test_platform?: string;
  model_version?: string;
  status?: string;
  search_enabled?: boolean;
  search_mode?: string;
  thinking_type?: string;
  reasoning_effort?: string;
  thinking_budget?: number;
  latency_ms?: number;
  requested_at?: string;
  question?: string;
  question_type?: string;
  product_category?: string;
  product_line?: string;
  purchase_stage?: string;
  scenario?: string;
  question_priority?: string;
  suggested_platforms?: string;
  target_brand_mentioned?: boolean;
  recommendation_strength?: string;
  citations_json?: string;
  response_text?: string;
  error_message?: string;
};

export type Analytics = {
  total_runs: number;
  success_runs: number;
  brand_mention_rate: number;
  owned_citation_rate: number;
  providers: Record<string, { total: number; mentioned: number; mention_rate: number; owned_citation_rate: number }>;
  competitors: Array<{ name: string; count: number }>;
};

export type AnalyticsSummary = {
  meta: {
    project_id: number;
    client_name: string;
    brand_name: string;
    batch_id: string;
    scope: "project" | "batch";
    generated_at: string;
  };
  entities: {
    target: { canonical_name: string; entity_type: string; aliases: string[] };
    competitors: Array<{ canonical_name: string; aliases: string[]; entity_type: string }>;
  };
  sample_quality: {
    planned: number;
    completed: number;
    valid: number;
    failed: number;
    pending: number;
    valid_rate: number;
    failure_rate: number;
  };
  visibility: {
    target_brand: string;
    valid_samples: number;
    mentioned: number;
    mention_rate: number;
    top1_rate: number;
    top3_rate: number;
    top5_rate: number;
    average_rank: number | null;
    avg_mentions_per_sample: number;
  };
  provider_breakdown: Array<{
    name: string;
    total: number;
    valid: number;
    failed: number;
    mention_rate: number;
    top3_rate: number;
    owned_citation_rate: number;
    average_rank: number | null;
    avg_latency_ms: number;
    high_risk: number;
  }>;
  question_type_breakdown: Array<{
    name: string;
    total: number;
    valid: number;
    failed: number;
    mention_rate: number;
    top3_rate: number;
    owned_citation_rate: number;
    average_rank: number | null;
    avg_latency_ms: number;
    high_risk: number;
    competitor_hit_rate: number;
  }>;
  competitor_risks: Array<{
    name: string;
    count: number;
    share_rate: number;
    target_absent_count: number;
    pressure_rate: number;
  }>;
  source_analysis: {
    owned_citation_rate: number;
    third_party_citation_rate: number;
    top_domains: Array<{ domain: string; count: number }>;
  };
  recommendations: string[];
  evidence: {
    failed: ModelRun[];
    high_risk: ModelRun[];
    brand_missed: ModelRun[];
  };
};
