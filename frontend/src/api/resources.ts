import { api } from "./client";
import type { Analytics, AnalyticsSummary, ModelConfig, ModelRun, Project, Question, SamplingBatch } from "./types";

export const authApi = {
  status: () => api<{ ok: boolean; auth_enabled: boolean; authenticated: boolean }>("/api/auth/status"),
  login: (password: string) => api<{ authenticated: boolean }>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => api<{ ok: boolean }>("/api/auth/logout", { method: "POST", body: JSON.stringify({}) })
};

export const systemApi = {
  health: () => api<{ ok: boolean; db: string; task_queue_backend: string }>("/api/health")
};

export const projectsApi = {
  list: () => api<{ projects: Project[] }>("/api/projects"),
  create: (payload: Partial<Project>) => api<{ id: number }>("/api/projects", { method: "POST", body: JSON.stringify(payload) }),
  update: (payload: Partial<Project> & { id: number }) => api<{ ok: boolean }>("/api/projects/update", { method: "POST", body: JSON.stringify(payload) }),
  remove: (id: number) => api<{ ok: boolean }>("/api/projects/delete", { method: "POST", body: JSON.stringify({ id }) })
};

export const questionsApi = {
  list: (projectId?: number | "all" | null) => api<{ questions: Question[] }>(`/api/questions${projectId ? `?project_id=${projectId}` : ""}`),
  seed: (projectId: number) => api<{ count: number }>("/api/questions/seed", { method: "POST", body: JSON.stringify({ project_id: projectId }) }),
  importText: (projectId: number, csvText: string) => api<{ count: number }>("/api/questions/import", { method: "POST", body: JSON.stringify({ project_id: projectId, csv_text: csvText }) }),
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
  rerunFailed: (batchId: string) => api<{ batch_id: string; total: number; status: string }>(`/api/batches/${encodeURIComponent(batchId)}/rerun_failed`, { method: "POST", body: JSON.stringify({}) }),
  pause: (batchId: string) => api<SamplingBatch>(`/api/batches/${encodeURIComponent(batchId)}/pause`, { method: "POST", body: JSON.stringify({}) }),
  resume: (batchId: string) => api<{ batch_id: string; total: number; status: string }>(`/api/batches/${encodeURIComponent(batchId)}/resume`, { method: "POST", body: JSON.stringify({}) }),
  progress: (batchId: string) => api<SamplingBatch>(`/api/runs/progress?batch_id=${encodeURIComponent(batchId)}`)
};

export const runsApi = {
  list: (projectId: number) => api<{ runs: ModelRun[] }>(`/api/runs?project_id=${projectId}`),
  start: (payload: Record<string, unknown>) => api<SamplingBatch>("/api/runs/start", { method: "POST", body: JSON.stringify(payload) })
};

export const analyticsApi = {
  get: (projectId: number) => api<Analytics>(`/api/analytics?project_id=${projectId}`),
  summary: (projectId: number, batchId?: string) => api<AnalyticsSummary>(`/api/analytics/summary?project_id=${projectId}${batchId ? `&batch_id=${encodeURIComponent(batchId)}` : ""}`)
};
