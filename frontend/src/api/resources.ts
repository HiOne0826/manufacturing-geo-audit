import { api } from "./client";
import type { Analytics, AnalyticsSummary, BatchComparison, BochaSearchConfig, ModelConfig, ModelRun, Project, QualityReview, ReportStatus, ReportVersion, Question, ReadinessStatus, SamplingBatch, SourceHealth } from "./types";

export const authApi = {
  status: () => api<{ ok: boolean; auth_enabled: boolean; authenticated: boolean }>("/api/auth/status"),
  login: (password: string) => api<{ authenticated: boolean }>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => api<{ ok: boolean }>("/api/auth/logout", { method: "POST", body: JSON.stringify({}) })
};

export const systemApi = {
  health: () => api<{ ok: boolean; db: string; task_queue_backend: string }>("/api/health"),
  ready: () => api<ReadinessStatus>("/api/health/ready"),
  sources: () => api<{ sources: SourceHealth[]; checked_at: string }>("/api/sources/health"),
  probeSource: (source: string) => api<{ source: string; probe_type: string; checked_at: string }>(`/api/sources/${encodeURIComponent(source)}/probe`, { method: "POST", body: JSON.stringify({}) })
};

export const tasksApi = {
  active: () => api<{ batches: SamplingBatch[]; stale: boolean; checked_at: string }>("/api/tasks/active")
};

export const settingsApi = {
  bochaSearch: () => api<BochaSearchConfig>("/api/settings/bocha-search"),
  updateBochaSearch: (payload: { api_key: string }) =>
    api<{ ok: boolean } & BochaSearchConfig>("/api/settings/bocha-search", { method: "POST", body: JSON.stringify(payload) })
};

export const projectsApi = {
  list: () => api<{ projects: Project[] }>("/api/projects"),
  create: (payload: Partial<Project>) => api<{ id: number }>("/api/projects", { method: "POST", body: JSON.stringify(payload) }),
  update: (payload: Partial<Project> & { id: number }) => api<{ ok: boolean }>("/api/projects/update", { method: "POST", body: JSON.stringify(payload) }),
  impact: (id: number) => api<{ impact: { question_count: number; batch_count: number; run_count: number; evaluation_count: number } }>(`/api/projects/${id}/impact`),
  archive: (id: number, archived = true) => api<{ ok: boolean }>("/api/projects/archive", { method: "POST", body: JSON.stringify({ id, archived }) }),
  remove: (id: number, confirmName: string) => api<{ ok: boolean }>("/api/projects/delete", { method: "POST", body: JSON.stringify({ id, confirm_name: confirmName }) })
};

export const questionsApi = {
  list: (projectId?: number | "all" | null) => api<{ questions: Question[] }>(`/api/questions${projectId ? `?project_id=${projectId}` : ""}`),
  seed: (projectId: number) => api<{ count: number }>("/api/questions/seed", { method: "POST", body: JSON.stringify({ project_id: projectId }) }),
  importText: (projectId: number, csvText: string) => api<{ count: number }>("/api/questions/import", { method: "POST", body: JSON.stringify({ project_id: projectId, csv_text: csvText }) }),
  previewFile: (projectId: number, fileName: string, fileBase64: string) => api<{ valid_rows: Record<string, string>[]; valid: number; duplicate: number; empty: number; invalid: number; skipped: number; issues: { row: number; reason: string; question?: string }[] }>("/api/questions/preview_file", { method: "POST", body: JSON.stringify({ project_id: projectId, file_name: fileName, file_base64: fileBase64 }) }),
  importFile: (projectId: number, fileName: string, fileBase64: string) => api<{ count: number }>("/api/questions/import_file", { method: "POST", body: JSON.stringify({ project_id: projectId, file_name: fileName, file_base64: fileBase64 }) }),
  importRows: (projectId: number, rows: Record<string, string>[]) => api<{ count: number }>("/api/questions/import_rows", { method: "POST", body: JSON.stringify({ project_id: projectId, rows }) }),
  update: (payload: Partial<Question> & { id: number }) => api<{ ok: boolean }>("/api/questions/update", { method: "POST", body: JSON.stringify(payload) }),
  remove: (id: number) => api<{ ok: boolean }>("/api/questions/delete", { method: "POST", body: JSON.stringify({ id }) })
};

export const modelsApi = {
  list: () => api<{ models: ModelConfig[]; presets: Record<string, Partial<ModelConfig>> }>("/api/models"),
  create: (payload: Partial<ModelConfig> & { api_key?: string }) => api<{ id: number }>("/api/models", { method: "POST", body: JSON.stringify(payload) }),
  update: (payload: Partial<ModelConfig> & { id: number; api_key?: string }) => api<{ ok: boolean }>("/api/models/update", { method: "POST", body: JSON.stringify(payload) }),
  test: (payload: { id: number }) => api<Record<string, unknown>>("/api/models/test", { method: "POST", body: JSON.stringify(payload) }),
  preset: (provider: string) => api<{ id: number }>("/api/models/preset", { method: "POST", body: JSON.stringify({ provider }) }),
  remove: (id: number) => api<{ ok: boolean }>("/api/models/delete", { method: "POST", body: JSON.stringify({ id }) })
};

export const batchesApi = {
  list: (projectId?: number | "all" | null) => api<{ batches: SamplingBatch[] }>(`/api/batches${projectId ? `?project_id=${projectId}` : ""}`),
  get: (batchId: string) => api<{ batch: SamplingBatch }>(`/api/batches/${encodeURIComponent(batchId)}`),
  runs: (batchId: string) => api<{ runs: ModelRun[] }>(`/api/batches/${encodeURIComponent(batchId)}/runs?latest=1`),
  runHistory: (batchId: string) => api<{ runs: ModelRun[] }>(`/api/batches/${encodeURIComponent(batchId)}/runs?history=1`),
  attempts: (batchId: string) => api<{ attempts: ModelRun[] }>(`/api/batches/${encodeURIComponent(batchId)}/attempts`),
  updateMetadata: (batchId: string, payload: { batch_name?: string; description?: string; purpose?: string; tags?: string[] }) => api<{ ok: boolean }>(`/api/batches/${encodeURIComponent(batchId)}/metadata`, { method: "POST", body: JSON.stringify(payload) }),
  archive: (batchId: string, archived = true) => api<{ batch: SamplingBatch }>(`/api/batches/${encodeURIComponent(batchId)}/archive`, { method: "POST", body: JSON.stringify({ archived }) }),
  retry: (batchId: string, payload: { scope: "all" | "source" | "tasks"; source?: string; task_ids?: string[] }) => api<{ batch_id: string; total: number; status: string }>(`/api/batches/${encodeURIComponent(batchId)}/retry`, { method: "POST", body: JSON.stringify(payload) }),
  rerunFailed: (batchId: string) => api<{ batch_id: string; total: number; status: string }>(`/api/batches/${encodeURIComponent(batchId)}/rerun_failed`, { method: "POST", body: JSON.stringify({}) }),
  pause: (batchId: string) => api<SamplingBatch>(`/api/batches/${encodeURIComponent(batchId)}/pause`, { method: "POST", body: JSON.stringify({}) }),
  resume: (batchId: string) => api<{ batch_id: string; total: number; status: string }>(`/api/batches/${encodeURIComponent(batchId)}/resume`, { method: "POST", body: JSON.stringify({}) }),
  progress: (batchId: string) => api<SamplingBatch>(`/api/runs/progress?batch_id=${encodeURIComponent(batchId)}`)
};

export const runsApi = {
  list: (projectId: number) => api<{ runs: ModelRun[] }>(`/api/runs?project_id=${projectId}`),
  preflight: (payload: Record<string, unknown>) => api<{ ready: boolean; blockers: { code: string; message: string; fix_path?: string }[]; warnings: { code: string; message: string }[]; total_tasks: number; estimated_duration: number | null; checked_at: string }>("/api/runs/preflight", { method: "POST", body: JSON.stringify(payload) }),
  start: (payload: Record<string, unknown>) => api<SamplingBatch>("/api/runs/start", { method: "POST", body: JSON.stringify(payload) })
};

export const analyticsApi = {
  get: (projectId: number) => api<Analytics>(`/api/analytics?project_id=${projectId}`),
  summary: (projectId: number, batchId?: string) => api<AnalyticsSummary>(`/api/analytics/summary?project_id=${projectId}${batchId ? `&batch_id=${encodeURIComponent(batchId)}` : ""}`)
};

export const qualityApi = {
  listForRun: (runId: string) => api<{ review: QualityReview | null }>(`/api/runs/${encodeURIComponent(runId)}/reviews`),
  review: (runId: string, payload: Pick<QualityReview, "decision" | "reason">) => api<{ review: QualityReview }>(`/api/runs/${encodeURIComponent(runId)}/review`, { method: "POST", body: JSON.stringify(payload) })
};

export const reportsApi = {
  list: (projectId: number, batchId?: string) => api<{ reports: ReportVersion[] }>(`/api/reports?project_id=${projectId}${batchId ? `&batch_id=${encodeURIComponent(batchId)}` : ""}`),
  create: (payload: { project_id: number; batch_id?: string; title: string }) => api<{ report: ReportVersion }>("/api/reports", { method: "POST", body: JSON.stringify(payload) }),
  transition: (id: string, status: ReportStatus) => api<{ report: ReportVersion }>(`/api/reports/${encodeURIComponent(id)}/status`, { method: "POST", body: JSON.stringify({ status }) }),
  freeze: (id: string) => api<{ report: ReportVersion }>(`/api/reports/${encodeURIComponent(id)}/freeze`, { method: "POST", body: JSON.stringify({}) }),
  compare: (projectId: number, baselineBatchId: string, comparisonBatchId: string) => api<BatchComparison>(`/api/analytics/compare?project_id=${projectId}&baseline_batch_id=${encodeURIComponent(baselineBatchId)}&batch_id=${encodeURIComponent(comparisonBatchId)}`)
};
