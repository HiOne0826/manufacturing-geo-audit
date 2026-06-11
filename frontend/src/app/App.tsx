import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { BarChart3, Boxes, Database, FileDown, Gauge, KeyRound, Layers3, ListChecks, Play, RefreshCw, Settings, Shield, SlidersHorizontal } from "lucide-react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiPath } from "../api/client";
import { analyticsApi, authApi, batchesApi, modelsApi, projectsApi, questionsApi, runsApi, systemApi } from "../api/resources";
import type { ModelConfig, Project, Question } from "../api/types";
import { EmptyState, Metric, PageTitle, StatusBadge } from "../components/common";
import { asCount, formatDateTime, pct } from "../utils/format";
import { useSelectionStore } from "../store/selectionStore";
import { queryClient } from "./queryClient";

type SamplingMode = "pure" | "search" | "compare";
type ModelFormState = Partial<ModelConfig> & { api_key?: string };

const navItems = [
  { to: "/", label: "总览", icon: Gauge },
  { to: "/projects", label: "项目", icon: Boxes },
  { to: "/questions", label: "问题库", icon: ListChecks },
  { to: "/models", label: "模型", icon: SlidersHorizontal },
  { to: "/sampling", label: "采样", icon: Play },
  { to: "/batches", label: "批次", icon: Layers3 },
  { to: "/analysis", label: "分析", icon: BarChart3 },
  { to: "/settings", label: "设置", icon: Settings }
];

export function App() {
  const auth = useQuery({ queryKey: ["auth"], queryFn: authApi.status });
  const [authMessage, setAuthMessage] = useState("");

  if (auth.isLoading) return <div className="boot">加载中</div>;
  if (auth.data?.auth_enabled && !auth.data.authenticated) {
    return <AuthGate message={authMessage} onMessage={setAuthMessage} />;
  }
  return <Shell />;
}

function AuthGate({ message, onMessage }: { message: string; onMessage: (value: string) => void }) {
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: authApi.login,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth"] }),
    onError: (error) => onMessage(error.message)
  });
  return (
    <main className="auth-screen">
      <form className="auth-panel" onSubmit={(event) => { event.preventDefault(); login.mutate(password); }}>
        <BrandBlock subtitle="内部访问" />
        <label>应用密码<input type="password" autoFocus value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <button type="submit" disabled={login.isPending}>进入工作台</button>
        <p className="danger-text">{message}</p>
      </form>
    </main>
  );
}

function Shell() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const health = useQuery({ queryKey: ["health"], queryFn: systemApi.health });
  const { projectId, setProjectId } = useSelectionStore();
  useEffect(() => {
    if (!projectId && projects.data?.projects?.[0]) setProjectId(projects.data.projects[0].id);
  }, [projectId, projects.data, setProjectId]);
  const logout = useMutation({ mutationFn: authApi.logout, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth"] }) });
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <BrandBlock subtitle="制造业 GEO 审计系统" />
        <nav>{navItems.map((item) => <NavLink key={item.to} to={item.to} end={item.to === "/"}><item.icon size={17} />{item.label}</NavLink>)}</nav>
      </aside>
      <section className="workspace">
        <header className="topbar">
          <div className="project-switcher">
            <span>当前项目</span>
            <select value={projectId || ""} onChange={(event) => setProjectId(Number(event.target.value) || null)}>
              {projects.data?.projects.map((project) => <option key={project.id} value={project.id}>{project.client_name} / {project.brand_name}</option>)}
            </select>
          </div>
          <div className="system-pills">
            <span><Database size={14} />{health.data?.task_queue_backend || "-"}</span>
            <span><Shield size={14} />{health.data?.ok ? "健康" : "未知"}</span>
            <button className="ghost" onClick={() => logout.mutate()}>退出</button>
          </div>
        </header>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/questions" element={<QuestionsPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/sampling" element={<SamplingPage />} />
          <Route path="/batches" element={<BatchesPage />} />
          <Route path="/batches/:batchId" element={<BatchDetailPage />} />
          <Route path="/analysis" element={<AnalysisPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </section>
    </div>
  );
}

function BrandBlock({ subtitle }: { subtitle: string }) {
  return (
    <div className="brand-block">
      <img className="brand-logo" src={`${import.meta.env.BASE_URL}brand/ostrich-brand-logo.png`} alt="鸵鸟 GEO" />
      <div>
        <strong>鸵鸟 GEO</strong>
        <span>{subtitle}</span>
      </div>
    </div>
  );
}

function Dashboard() {
  const { projectId } = useSelectionStore();
  const projects = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const models = useQuery({ queryKey: ["models"], queryFn: modelsApi.list });
  const batches = useQuery({ queryKey: ["batches", "all"], queryFn: () => batchesApi.list("all"), refetchInterval: 2500 });
  const analytics = useQuery({ queryKey: ["analytics", projectId], queryFn: () => analyticsApi.get(projectId!), enabled: Boolean(projectId) });
  const recent = batches.data?.batches || [];
  const running = recent.filter((item) => ["queued", "running"].includes(item.status)).length;
  return (
    <main className="page">
      <PageTitle title="系统总览" description="查看运行状态、批次吞吐和模型可用性。" />
      <section className="metrics-grid">
        <Metric label="项目数" value={projects.data?.projects.length || 0} />
        <Metric label="可用模型" value={(models.data?.models || []).filter((item) => item.active).length} hint={`${(models.data?.models || []).filter((item) => item.has_key).length} 个已配置 Key`} />
        <Metric label="运行中批次" value={running} />
        <Metric label="品牌命中率" value={`${analytics.data?.brand_mention_rate ?? 0}%`} />
      </section>
      <section className="two-column">
        <Panel title="最近批次">
          <BatchTable batches={recent.slice(0, 8)} />
        </Panel>
        <Panel title="模型可用性">
          <div className="model-health-list">{(models.data?.models || []).slice(0, 10).map((model) => <ModelHealth key={model.id} model={model} />)}</div>
        </Panel>
      </section>
    </main>
  );
}

function ProjectsPage() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const [draft, setDraft] = useState({ client_name: "示例制造企业", brand_name: "目标品牌", product_category: "工业自动化设备", target_region: "华东地区", competitors: "竞品A;竞品B" });
  const create = useMutation({ mutationFn: projectsApi.create, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] }) });
  const remove = useMutation({ mutationFn: projectsApi.remove, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] }) });
  return (
    <main className="page">
      <PageTitle title="项目" description="维护客户、品牌、品类和竞品边界。" />
      <Panel title="新增项目">
        <form className="form-grid compact" onSubmit={(event) => { event.preventDefault(); create.mutate(draft); }}>
          {(["client_name", "brand_name", "product_category", "target_region", "competitors"] as const).map((key) => <label key={key}>{projectLabels[key]}<input value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
          <button type="submit">保存项目</button>
        </form>
      </Panel>
      <Panel title="项目列表">
        <div className="data-table">
          <table><thead><tr><th>客户 / 品牌</th><th>品类</th><th>地区</th><th>竞品</th><th>操作</th></tr></thead><tbody>
            {projects.data?.projects.map((project) => <tr key={project.id}><td><strong>{project.client_name}</strong><span>{project.brand_name}</span></td><td>{project.product_category || "-"}</td><td>{project.target_region || "-"}</td><td>{project.competitors || "-"}</td><td><button className="ghost" onClick={() => remove.mutate(project.id)}>删除</button></td></tr>)}
          </tbody></table>
        </div>
      </Panel>
    </main>
  );
}

const projectLabels = { client_name: "客户名称", brand_name: "品牌名称", product_category: "产品品类", target_region: "目标地区", competitors: "竞品列表" };

function QuestionsPage() {
  const { projectId } = useSelectionStore();
  const questions = useQuery({ queryKey: ["questions", projectId], queryFn: () => questionsApi.list(projectId), enabled: Boolean(projectId) });
  const [csvText, setCsvText] = useState("汽车白车身多材料连接，国内有哪些FDS热熔螺接设备品牌值得推荐？\n新能源汽车电池PACK装配，国内有哪些FDS热熔螺接设备品牌值得推荐？");
  const importText = useMutation({ mutationFn: () => questionsApi.importText(projectId!, csvText), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["questions", projectId] }) });
  const seed = useMutation({ mutationFn: () => questionsApi.seed(projectId!), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["questions", projectId] }) });
  const remove = useMutation({ mutationFn: questionsApi.remove, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["questions", projectId] }) });
  return (
    <main className="page">
      <PageTitle title="问题库" description="按项目导入和维护采样问题。" action={<button disabled={!projectId} onClick={() => seed.mutate()}>生成模板问题</button>} />
      <section className="two-column">
        <Panel title="快速导入">
          <textarea className="tall" value={csvText} onChange={(event) => setCsvText(event.target.value)} />
          <button disabled={!projectId || importText.isPending} onClick={() => importText.mutate()}>识别并导入</button>
        </Panel>
        <Panel title={`问题列表 ${questions.data?.questions.length || 0}`}>
          <QuestionTable questions={questions.data?.questions || []} onDelete={(id) => remove.mutate(id)} />
        </Panel>
      </section>
    </main>
  );
}

function QuestionTable({ questions, onDelete }: { questions: Question[]; onDelete: (id: number) => void }) {
  if (!questions.length) return <EmptyState title="当前项目还没有问题" />;
  return <div className="data-table dense"><table><thead><tr><th>问题</th><th>类型</th><th>阶段</th><th>优先级</th><th></th></tr></thead><tbody>{questions.map((q) => <tr key={q.id}><td className="wide-cell">{q.question}</td><td>{q.question_type}</td><td>{q.purchase_stage || "-"}</td><td>{q.priority || "-"}</td><td><button className="ghost" onClick={() => onDelete(q.id)}>删除</button></td></tr>)}</tbody></table></div>;
}

function ModelsPage() {
  const models = useQuery({ queryKey: ["models"], queryFn: modelsApi.list });
  const [draft, setDraft] = useState<ModelFormState>(newModelDraft());
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const create = useMutation({
    mutationFn: modelsApi.create,
    onSuccess: () => {
      setDraft(newModelDraft());
      setShowCreateModal(false);
      queryClient.invalidateQueries({ queryKey: ["models"] });
    }
  });
  const update = useMutation({
    mutationFn: modelsApi.update,
    onSuccess: () => {
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ["models"] });
    }
  });
  const test = useMutation({ mutationFn: modelsApi.test });
  return (
    <main className="page">
      <PageTitle title="模型" description="查看服务商能力、Key 状态和采样默认参数。" action={<button onClick={() => setShowCreateModal(true)}>新增模型</button>} />
      {showCreateModal ? (
        <Modal title="新增模型" onClose={() => setShowCreateModal(false)}>
          <div className="preset-row">
            {Object.keys(models.data?.presets || {}).map((provider) => (
              <button key={provider} className="ghost" type="button" onClick={() => setDraft({ ...newModelDraft(), ...(models.data?.presets[provider] || {}), api_key: "" })}>{provider}</button>
            ))}
          </div>
          <ModelForm value={draft} onChange={setDraft} submitLabel="保存模型" onSubmit={() => create.mutate(draft)} onCancel={() => setShowCreateModal(false)} />
        </Modal>
      ) : null}
      {editing ? (
        <Modal title={`编辑模型：${editing.label}`} onClose={() => setEditing(null)}>
          <ModelForm value={{ ...editing, api_key: "" }} onChange={(value) => setEditing({ ...editing, ...value })} submitLabel="保存设置" isEdit onSubmit={() => update.mutate({ ...editing, api_key: editing.api_key || "__KEEP__" })} onCancel={() => setEditing(null)} />
        </Modal>
      ) : null}
      <div className="model-grid">{models.data?.models.map((model) => <ModelCard key={model.id} model={model} onEdit={() => setEditing(model)} onTest={() => test.mutate({ id: model.id })} />)}</div>
      {test.data ? <pre className="result-box">{JSON.stringify(test.data, null, 2)}</pre> : null}
      {test.error ? <div className="error-box">{test.error.message.includes("真实模型调用默认关闭") ? "真实模型测试当前被后端安全开关拦截。需要本地验收真实调用时，用 ALLOW_LIVE_MODEL_CALLS=1 重启 python3 app.py。" : test.error.message}</div> : null}
    </main>
  );
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <section className="modal-panel">
        <header className="modal-header">
          <h2>{title}</h2>
          <button className="ghost" type="button" onClick={onClose}>关闭</button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

function newModelDraft(): ModelFormState {
  return {
    provider: "mock",
    label: "Mock",
    model: "mock-model",
    api_key: "",
    model_type: "chat",
    priority: 100,
    daily_limit: 0,
    supports_pure: true,
    supports_tool_calling: true,
    active: true
  };
}

function ModelForm({ value, onChange, onSubmit, onCancel, submitLabel, isEdit = false }: { value: ModelFormState; onChange: (value: ModelFormState) => void; onSubmit: () => void; onCancel?: () => void; submitLabel: string; isEdit?: boolean }) {
  const set = (key: keyof ModelFormState, next: unknown) => onChange({ ...value, [key]: next });
  const checkbox = (key: keyof ModelFormState, label: string) => (
    <label className="checkbox-field"><input type="checkbox" checked={Boolean(value[key])} onChange={(event) => set(key, event.target.checked)} />{label}</label>
  );
  return (
    <form className="model-form" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <div className="form-grid">
        <label>模型名称<input value={value.label || ""} onChange={(event) => set("label", event.target.value)} /></label>
        <label>服务商标识<input value={value.provider || ""} onChange={(event) => set("provider", event.target.value)} /></label>
        <label>API 家族<input value={value.api_family || ""} onChange={(event) => set("api_family", event.target.value)} /></label>
        <label>模型类型<select value={value.model_type || "chat"} onChange={(event) => set("model_type", event.target.value)}><option value="chat">聊天模型</option><option value="embedding">Embedding</option></select></label>
        <label className="wide">模型 ID<input value={value.model || ""} onChange={(event) => set("model", event.target.value)} /></label>
        <label className="wide">模型版本候选<input value={value.model_version || ""} onChange={(event) => set("model_version", event.target.value)} placeholder="多个版本可用逗号、分号或换行分隔" /></label>
        <label className="wide">API 地址<input value={value.api_base || ""} onChange={(event) => set("api_base", event.target.value)} /></label>
        <label className="wide">API Key<input type="password" value={value.api_key || ""} onChange={(event) => set("api_key", event.target.value)} placeholder={isEdit ? "留空表示保持原 Key" : ""} /></label>
        <label>优先级<input type="number" value={Number(value.priority ?? 100)} onChange={(event) => set("priority", Number(event.target.value) || 0)} /></label>
        <label>每日限制<input type="number" value={Number(value.daily_limit ?? 0)} onChange={(event) => set("daily_limit", Number(event.target.value) || 0)} /></label>
      </div>
      <div className="capability-grid">
        {checkbox("supports_pure", "纯模型")}
        {checkbox("supports_search", "联网搜索")}
        {checkbox("supports_reasoning", "深度思考")}
        {checkbox("supports_citation", "引用返回")}
        {checkbox("supports_site_filter", "站点筛选")}
        {checkbox("supports_time_filter", "时间筛选")}
        {checkbox("supports_user_location", "地区定位")}
        {checkbox("supports_tool_calling", "工具调用")}
        {checkbox("active", "启用")}
      </div>
      <div className="form-grid">
        <label className="wide">联网模式说明<input value={value.web_search_mode || ""} onChange={(event) => set("web_search_mode", event.target.value)} /></label>
        <label className="wide">联网参数路径<input value={value.web_search_param_path || ""} onChange={(event) => set("web_search_param_path", event.target.value)} /></label>
        <label className="wide">深度思考参数路径<input value={value.reasoning_param_path || ""} onChange={(event) => set("reasoning_param_path", event.target.value)} /></label>
        <label className="wide">思考档位/预算说明<input value={value.reasoning_levels || ""} onChange={(event) => set("reasoning_levels", event.target.value)} /></label>
        <label className="wide">引用参数路径<input value={value.citation_param_path || ""} onChange={(event) => set("citation_param_path", event.target.value)} /></label>
        <label className="wide">备注<textarea value={value.notes || ""} onChange={(event) => set("notes", event.target.value)} /></label>
      </div>
      <div className="inline-actions"><button type="submit">{submitLabel}</button>{onCancel ? <button className="ghost" type="button" onClick={onCancel}>取消</button> : null}</div>
    </form>
  );
}

function ModelCard({ model, onTest, onEdit }: { model: ModelConfig; onTest: () => void; onEdit: () => void }) {
  const defaults = model.sampling_defaults || {};
  return <article className="model-card"><header><div><strong>{model.label}</strong><span>{model.provider} / {model.model}</span></div><span className={model.has_key ? "key-ok" : "key-missing"}><KeyRound size={14} />{model.has_key ? model.api_key_masked || "已配置" : "未配置"}</span></header><div className="tag-row">{model.supports_search ? <span>联网</span> : null}{model.supports_reasoning ? <span>思考</span> : null}{model.supports_citation ? <span>引用</span> : null}{model.active ? <span>启用</span> : <span>停用</span>}</div><dl><dt>temperature</dt><dd>{String(defaults.temperature ?? "模型默认")}</dd><dt>reasoning</dt><dd>{String(defaults.reasoning_effort ?? "模型默认")}</dd><dt>api_base</dt><dd>{model.api_base || "-"}</dd><dt>note</dt><dd>{String(defaults.defaults_note ?? "-")}</dd></dl><div className="inline-actions"><button className="ghost" onClick={onEdit}>编辑设置</button><button className="ghost" onClick={onTest}>测试</button></div></article>;
}

function SamplingPage() {
  const { projectId } = useSelectionStore();
  const questions = useQuery({ queryKey: ["questions", projectId], queryFn: () => questionsApi.list(projectId), enabled: Boolean(projectId) });
  const models = useQuery({ queryKey: ["models"], queryFn: modelsApi.list });
  const [selected, setSelected] = useState<Record<number, { mode: SamplingMode; reasoning_enabled: boolean }>>({});
  const [repeatCount, setRepeatCount] = useState(1);
  const [activeBatch, setActiveBatch] = useState<string>("");
  const runModels = useMemo(
    () =>
      Object.entries(selected).flatMap(([id, cfg]) => {
        const model_config_id = Number(id);
        if (cfg.mode === "compare") {
          return [
            { model_config_id, search_enabled: false, reasoning_enabled: cfg.reasoning_enabled, comparison_mode: "pure" },
            { model_config_id, search_enabled: true, reasoning_enabled: cfg.reasoning_enabled, comparison_mode: "search" }
          ];
        }
        return [{ model_config_id, search_enabled: cfg.mode === "search", reasoning_enabled: cfg.reasoning_enabled }];
      }),
    [selected]
  );
  const start = useMutation({
    mutationFn: () => runsApi.start({ project_id: projectId, repeat_count: repeatCount, models: runModels }),
    onSuccess: (data) => { setActiveBatch(data.batch_id); queryClient.invalidateQueries({ queryKey: ["batches"] }); }
  });
  const progress = useQuery({ queryKey: ["progress", activeBatch], queryFn: () => batchesApi.progress(activeBatch), enabled: Boolean(activeBatch), refetchInterval: (query) => query.state.data?.status === "completed" || query.state.data?.status === "failed" ? false : 1200 });
  const activeModels = (models.data?.models || []).filter((m) => m.active);
  const searchTaskCount = runModels.filter((item) => item.search_enabled).length;
  const totalTasks = (questions.data?.questions.length || 0) * runModels.length * repeatCount;
  return (
    <main className="page sampling-page">
      <PageTitle title="采样" description="选择问题范围和模型矩阵，对比模型本体与联网搜索结果。" />
      <section className="sampling-grid">
        <Panel title="范围"><Metric label="当前问题数" value={questions.data?.questions.length || 0} /><label>重复次数<input type="number" min={1} max={10} value={repeatCount} onChange={(event) => setRepeatCount(Number(event.target.value) || 1)} /></label></Panel>
        <Panel title="任务估算"><Metric label="已选模型" value={Object.keys(selected).length} /><Metric label="联网任务配置" value={searchTaskCount} /><Metric label="预计任务" value={totalTasks} /><button disabled={!projectId || !totalTasks || start.isPending} onClick={() => start.mutate()}><Play size={15} />启动采样</button></Panel>
        <Panel title="运行状态">{activeBatch ? <><StatusBadge status={progress.data?.status || "queued"} /><ProgressBar batch={progress.data} /><Link to={`/batches/${activeBatch}`}>查看批次详情</Link></> : <EmptyState title="尚未启动采样" />}</Panel>
      </section>
      <Panel title="模型矩阵">
        <div className="model-matrix">
          {activeModels.map((model) => {
            const config = selected[model.id];
            const enabled = Boolean(config);
            const mode = config?.mode || "pure";
            return (
              <article key={model.id} className={`matrix-card ${enabled ? "is-selected" : ""}`}>
                <header>
                  <label className="matrix-select">
                    <input
                      type="checkbox"
                      checked={enabled}
                      onChange={(event) =>
                        setSelected((prev) =>
                          event.target.checked
                            ? { ...prev, [model.id]: { mode: "pure", reasoning_enabled: false } }
                            : (Object.fromEntries(Object.entries(prev).filter(([id]) => Number(id) !== model.id)) as typeof prev)
                        )
                      }
                    />
                    <strong>{model.label}</strong>
                  </label>
                  <span className={model.has_key ? "key-ok" : "key-missing"}>{model.has_key ? "Key 已配置" : "缺少 Key"}</span>
                </header>
                <p>{model.provider} / {model.model}</p>
                <div className="sampling-mode-group">
                  <button className={mode === "pure" ? "is-active" : ""} type="button" disabled={!enabled} onClick={() => setSelected((prev) => ({ ...prev, [model.id]: { ...prev[model.id], mode: "pure" } }))}>本体</button>
                  <button className={mode === "search" ? "is-active" : ""} type="button" disabled={!enabled || !model.supports_search} onClick={() => setSelected((prev) => ({ ...prev, [model.id]: { ...prev[model.id], mode: "search" } }))}>联网</button>
                  <button className={mode === "compare" ? "is-active" : ""} type="button" disabled={!enabled || !model.supports_search} onClick={() => setSelected((prev) => ({ ...prev, [model.id]: { ...prev[model.id], mode: "compare" } }))}>本体+联网</button>
                </div>
                <label className="matrix-toggle">
                  <input
                    type="checkbox"
                    disabled={!enabled || !model.supports_reasoning}
                    checked={Boolean(config?.reasoning_enabled)}
                    onChange={(event) => setSelected((prev) => ({ ...prev, [model.id]: { ...prev[model.id], reasoning_enabled: event.target.checked } }))}
                  />
                  深度思考
                </label>
                <dl>
                  <dt>默认温度</dt><dd>{String(model.sampling_defaults?.temperature ?? "模型默认")}</dd>
                  <dt>联网能力</dt><dd>{model.supports_search ? "支持" : "不支持"}</dd>
                  <dt>说明</dt><dd>{String(model.sampling_defaults?.defaults_note ?? "按服务商默认参数运行")}</dd>
                </dl>
              </article>
            );
          })}
        </div>
      </Panel>
    </main>
  );
}

function BatchesPage() {
  const { projectId } = useSelectionStore();
  const batches = useQuery({ queryKey: ["batches", projectId], queryFn: () => batchesApi.list(projectId || "all"), refetchInterval: 2500 });
  return <main className="page"><PageTitle title="批次" description="集中查看后台采样任务、状态和导出入口。" /><Panel title="批次列表"><BatchTable batches={batches.data?.batches || []} /></Panel></main>;
}

function BatchDetailPage() {
  const { batchId = "" } = useParams();
  const batch = useQuery({ queryKey: ["batch", batchId], queryFn: () => batchesApi.get(batchId), refetchInterval: 2500 });
  const runs = useQuery({ queryKey: ["batch-runs", batchId], queryFn: () => batchesApi.runs(batchId), refetchInterval: 3000 });
  const rerun = useMutation({ mutationFn: () => batchesApi.rerunFailed(batchId), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["batch", batchId] }) });
  const rows = runs.data?.runs || [];
  const byProvider = useMemo(() => Object.entries(rows.reduce<Record<string, { total: number; failed: number; latency: number }>>((acc, row) => { const key = row.provider || "unknown"; acc[key] ||= { total: 0, failed: 0, latency: 0 }; acc[key].total += 1; acc[key].failed += row.status === "failed" ? 1 : 0; acc[key].latency += Number(row.latency_ms || 0); return acc; }, {})), [rows]);
  return <main className="page"><PageTitle title={`批次 ${batchId}`} description="查看进度、模型表现、失败原因和导出。" action={<div className="inline-actions"><a className="button ghost" href={apiPath(`/api/export/batches/${batchId}/runs.xls`)} target="_blank">导出明细</a><button onClick={() => rerun.mutate()}>重跑失败</button></div>} /><Panel title="进度">{batch.data?.batch ? <><StatusBadge status={batch.data.batch.status} /><ProgressBar batch={batch.data.batch} /></> : null}</Panel><section className="two-column"><Panel title="模型摘要"><div className="provider-summary">{byProvider.map(([provider, value]) => <div key={provider}><strong>{provider}</strong><span>{value.total} 次 / 失败 {value.failed}</span><em>{value.total ? Math.round(value.latency / value.total) : 0} ms</em></div>)}</div></Panel><Panel title="失败原因"><div className="failure-list">{rows.filter((row) => row.status === "failed").slice(0, 8).map((row) => <p key={row.id}>{row.provider}: {row.error_message}</p>)}{!rows.some((row) => row.status === "failed") ? <EmptyState title="暂无失败任务" /> : null}</div></Panel></section><Panel title="运行明细"><RunsTable runs={rows} /></Panel></main>;
}

function AnalysisPage() {
  const { projectId } = useSelectionStore();
  const analytics = useQuery({ queryKey: ["analytics", projectId], queryFn: () => analyticsApi.get(projectId!), enabled: Boolean(projectId) });
  const providers = Object.entries(analytics.data?.providers || {});
  const chartRows = providers.map(([name, item]) => ({ name, 命中率: item.mention_rate, 官网引用率: item.owned_citation_rate }));
  return <main className="page"><PageTitle title="分析" description="查看品牌命中、官网引用和竞品共现。" /><section className="metrics-grid"><Metric label="总运行" value={analytics.data?.total_runs || 0} /><Metric label="成功运行" value={analytics.data?.success_runs || 0} /><Metric label="品牌命中率" value={`${analytics.data?.brand_mention_rate || 0}%`} /><Metric label="官网引用率" value={`${analytics.data?.owned_citation_rate || 0}%`} /></section><Panel title="模型表现图"><div className="chart-box">{chartRows.length ? <ResponsiveContainer width="100%" height={260}><BarChart data={chartRows}><XAxis dataKey="name" tick={{ fontSize: 12 }} /><YAxis /><Tooltip /><Bar dataKey="命中率" fill="#2868d8" /><Bar dataKey="官网引用率" fill="#18a77b" /></BarChart></ResponsiveContainer> : <EmptyState title="暂无分析数据" />}</div></Panel><section className="two-column"><Panel title="模型表现表"><div className="data-table"><table><thead><tr><th>模型 / 模式</th><th>运行数</th><th>命中率</th><th>官网引用率</th></tr></thead><tbody>{providers.map(([name, item]) => <tr key={name}><td>{name}</td><td>{item.total}</td><td>{item.mention_rate}%</td><td>{item.owned_citation_rate}%</td></tr>)}</tbody></table></div></Panel><Panel title="竞品共现"><div className="data-table"><table><tbody>{analytics.data?.competitors.map((item) => <tr key={item.name}><td>{item.name}</td><td>{item.count}</td></tr>)}</tbody></table></div></Panel></section></main>;
}

function SettingsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: systemApi.health });
  return (
    <main className="page">
      <PageTitle title="设置" description="运行模式、Agent/MCP 接入、安全边界和前端构建状态。" />
      <section className="metrics-grid">
        <Metric label="健康状态" value={health.data?.ok ? "正常" : "未知"} />
        <Metric label="任务后端" value={health.data?.task_queue_backend || "-"} />
        <Metric label="前端入口" value="React / Vite" />
        <Metric label="Key 返回策略" value="脱敏" />
      </section>
      <section className="settings-grid">
        <Panel title="系统运行">
          <div className="settings-list">
            <div><span>数据库</span><code>{health.data?.db || "-"}</code></div>
            <div><span>任务队列</span><code>{health.data?.task_queue_backend || "-"}</code></div>
            <div><span>本地开发</span><code>python3 app.py</code></div>
            <div><span>前端构建</span><code>cd frontend && npm run build</code></div>
          </div>
        </Panel>
        <Panel title="Agent / MCP">
          <div className="settings-list">
            <div><span>启动 MCP</span><code>python3 -m mcp.server</code></div>
            <div><span>后端地址</span><code>GEO_AUDIT_BASE_URL=http://127.0.0.1:8765</code></div>
            <div><span>鉴权</span><code>AGENT_API_TOKEN=内部 token</code></div>
          </div>
          <p className="muted">MCP wrapper 只调用 Agent API，不读取模型服务商 API Key。</p>
        </Panel>
        <Panel title="公网门禁">
          <div className="check-list">
            <label><input type="checkbox" checked readOnly />Nginx Basic Auth</label>
            <label><input type="checkbox" checked readOnly />应用全局密码 APP_PASSWORD</label>
            <label><input type="checkbox" checked readOnly />API Key 不明文返回</label>
            <label><input type="checkbox" checked readOnly />真实模型调用受 ALLOW_LIVE_MODEL_CALLS 控制</label>
          </div>
        </Panel>
      </section>
    </main>
  );
}

function BatchTable({ batches }: { batches: Array<{ batch_id: string; status: string; created_at?: string; total_count?: number; completed_count?: number; success_count?: number; failed_count?: number; total?: number; completed?: number; success?: number; failed?: number }> }) {
  if (!batches.length) return <EmptyState title="暂无批次" />;
  return <div className="data-table"><table><thead><tr><th>批次</th><th>状态</th><th>进度</th><th>成功 / 失败</th><th>创建时间</th></tr></thead><tbody>{batches.map((batch) => { const c = asCount(batch); return <tr key={batch.batch_id}><td><Link to={`/batches/${batch.batch_id}`}>{batch.batch_id}</Link></td><td><StatusBadge status={batch.status} /></td><td>{c.completed} / {c.total}</td><td>{c.success} / {c.failed}</td><td>{formatDateTime(batch.created_at)}</td></tr>; })}</tbody></table></div>;
}

function RunsTable({ runs }: { runs: Array<{ id: number; provider?: string; model?: string; status?: string; latency_ms?: number; question_type?: string; response_text?: string; requested_at?: string }> }) {
  if (!runs.length) return <EmptyState title="暂无运行明细" />;
  return <div className="data-table dense"><table><thead><tr><th>模型</th><th>状态</th><th>耗时</th><th>问题类型</th><th>回答摘要</th><th>时间</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id}><td>{run.provider} / {run.model}</td><td>{run.status}</td><td>{run.latency_ms || 0} ms</td><td>{run.question_type || "-"}</td><td className="wide-cell">{(run.response_text || "").slice(0, 140)}</td><td>{formatDateTime(run.requested_at)}</td></tr>)}</tbody></table></div>;
}

function ProgressBar({ batch }: { batch?: { total_count?: number; completed_count?: number; total?: number; completed?: number } }) {
  const c = asCount(batch || {});
  return <div className="progress"><div style={{ width: `${pct(c.completed, c.total)}%` }} /><span>{c.completed} / {c.total}</span></div>;
}

function ModelHealth({ model }: { model: ModelConfig }) {
  return <div className="model-health"><strong>{model.label}</strong><span>{model.provider}</span><em>{model.has_key ? "Key 已配置" : "缺少 Key"}</em></div>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="panel"><h2>{title}</h2>{children}</section>;
}
