import { Fragment, lazy, Suspense, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Archive, CheckCircle2, ChevronDown, ChevronUp, ClipboardCheck, Edit3, ExternalLink, FileDown, FileSpreadsheet, KeyRound, Pause, Play, RefreshCw, RotateCcw, Trash2, Upload } from "lucide-react";
import { ApiError, apiPath } from "../api/client";
import { analyticsApi, batchesApi, modelsApi, projectsApi, qualityApi, questionsApi, reportsApi, runsApi, settingsApi, systemApi } from "../api/resources";
import type { AnalyticsSummary, BochaSearchConfig, Citation, ModelConfig, ModelRun, Project, QualityDecision, Question, ReportStatus, SamplingBatch, SourceRunStatus } from "../api/types";
import { EmptyState, Metric, PageTitle, Pagination, StatusBadge, statusLabel } from "../components/common";
import { AsyncBoundary, ConfirmDialog, useDialogFocus, useToast } from "../components/ui";
import { asCount, formatDateTime, pct } from "../utils/format";
import { useSelectionStore } from "../store/selectionStore";
import { queryClient } from "../app/queryClient";

type SamplingMode = "pure" | "search" | "compare";
type ModelFormState = Partial<ModelConfig> & { api_key?: string };
const AnalyticsChart = lazy(() => import("./AnalyticsChart"));

function runPlatform(row: Pick<ModelRun, "test_platform" | "provider">) {
  return row.test_platform || row.provider || "unknown";
}

export function citationUrls(value?: string | Citation[]) {
  if (!value) return "";
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    const items = Array.isArray(parsed) ? parsed : [];
    const urls = items
      .map((item) => String(item?.url || item?.link || item?.uri || "").trim())
      .filter((url) => {
        try {
          const protocol = new URL(url).protocol;
          return protocol === "http:" || protocol === "https:";
        } catch {
          return false;
        }
      });
    return Array.from(new Set(urls)).join("; ");
  } catch {
    return "";
  }
}

function citationUrlList(value?: string | Citation[]) {
  return citationUrls(value).split("; ").filter(Boolean);
}

function isTerminalStatus(status?: string) {
  return status === "completed" || status === "failed" || status === "failed_system" || status === "cancelled" || status === "paused";
}

function isPausableStatus(status?: string) {
  return status === "queued" || status === "running";
}

function isRerunnableStatus(status?: string) {
  return status !== "queued" && status !== "running" && status !== "pause_requested";
}

function isResumableStatus(status?: string) {
  return status === "paused" || status === "pause_requested" || status === "failed" || status === "failed_system";
}

function resumeActionLabel(status?: string, pending = false) {
  if (status === "pause_requested") return pending ? "正在取消" : "取消暂停";
  return pending ? "正在继续" : "继续执行";
}

function hasIncompleteWork(batch?: SamplingBatch) {
  if (!batch) return false;
  return Number(batch.completed ?? batch.completed_count ?? 0) < Number(batch.total ?? batch.total_count ?? 0);
}

export function Dashboard() {
  const { projectId } = useSelectionStore();
  const projects = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const models = useQuery({ queryKey: ["models"], queryFn: modelsApi.list });
  const batches = useQuery({ queryKey: ["batches", "all"], queryFn: () => batchesApi.list("all"), refetchInterval: 2500 });
  const questions = useQuery({ queryKey: ["questions", projectId], queryFn: () => questionsApi.list(projectId), enabled: Boolean(projectId) });
  const sources = useQuery({ queryKey: ["sources", "health"], queryFn: systemApi.sources, enabled: Boolean(projectId) });
  const analytics = useQuery({ queryKey: ["analytics", projectId], queryFn: () => analyticsApi.get(projectId!), enabled: Boolean(projectId) });
  const recent = batches.data?.batches || [];
  const running = recent.filter((item) => ["queued", "running"].includes(item.status)).length;
  const current = projects.data?.projects.find((project) => project.id === projectId);
  const projectBatches = recent.filter((batch) => batch.project_id === projectId);
  const readySources = (sources.data?.sources || []).filter((source) => source.modes.pure.ready || source.modes.search.ready).length;
  const readiness = [
    { label: "项目档案", ready: Boolean(current?.client_name && current?.brand_name && current?.product_category), detail: current?.product_category || "补充产品品类", to: "/projects" },
    { label: "问题库", ready: Boolean(questions.data?.questions.length), detail: `${questions.data?.questions.length || 0} 个问题`, to: "/questions" },
    { label: "信息源", ready: readySources > 0, detail: `${readySources} / ${sources.data?.sources.length || 0} 可用`, to: "/settings" },
    { label: "采样批次", ready: projectBatches.length > 0, detail: projectBatches.length ? `${projectBatches.length} 个批次` : "尚未启动", to: "/sampling" },
    { label: "分析数据", ready: Boolean(analytics.data?.total_runs), detail: analytics.data?.total_runs ? `${analytics.data.total_runs} 条结果` : "等待采样结果", to: "/analysis" }
  ];
  return (
    <main className="page">
      <PageTitle title="系统总览" description="查看运行状态、批次吞吐和模型可用性。" />
      <section className="metrics-grid">
        <Metric label="项目数" value={projects.data?.projects.length || 0} />
        <Metric label="可用模型" value={(models.data?.models || []).filter((item) => item.active).length} hint={`${(models.data?.models || []).filter((item) => item.has_key).length} 个已配置 Key`} />
        <Metric label="运行中批次" value={running} />
        <Metric label="品牌命中率" value={`${analytics.data?.brand_mention_rate ?? 0}%`} />
      </section>
      {projectId ? <Panel title={`${current?.brand_name || "当前项目"} · 项目准备度`}><AsyncBoundary loading={questions.isLoading || sources.isLoading || analytics.isLoading} error={questions.error || sources.error || analytics.error} onRetry={() => { questions.refetch(); sources.refetch(); analytics.refetch(); }}><div className="readiness-journey">{readiness.map((item, index) => <Link className={item.ready ? "is-ready" : "is-pending"} to={`${item.to}?project_id=${projectId}`} key={item.label}><span>{item.ready ? <CheckCircle2 size={17} /> : index + 1}</span><div><strong>{item.label}</strong><em>{item.detail}</em></div></Link>)}</div><p className="muted">建议下一步：{readiness.find((item) => !item.ready)?.label || "查看分析并准备交付"}</p></AsyncBoundary></Panel> : null}
      <section className="two-column">
        <Panel title="最近批次">
          <AsyncBoundary loading={batches.isLoading} error={batches.error} empty={!recent.length} emptyLabel="暂无批次" onRetry={() => batches.refetch()}><BatchTable batches={recent.slice(0, 8)} /></AsyncBoundary>
        </Panel>
        <Panel title="模型可用性">
          <AsyncBoundary loading={models.isLoading} error={models.error} empty={!models.data?.models.length} emptyLabel="暂无模型配置" onRetry={() => models.refetch()}><div className="model-health-list">{(models.data?.models || []).slice(0, 10).map((model) => <ModelHealth key={model.id} model={model} />)}</div></AsyncBoundary>
        </Panel>
      </section>
    </main>
  );
}

export function ProjectsPage() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [editing, setEditing] = useState<Project | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [draftDirty, setDraftDirty] = useState(false);
  const [draft, setDraft] = useState({ client_name: "示例制造企业", brand_name: "目标品牌", product_category: "工业自动化设备", target_region: "华东地区", competitors: "竞品A;竞品B" });
  const create = useMutation({ mutationFn: projectsApi.create, onSuccess: () => { setDraftDirty(false); queryClient.invalidateQueries({ queryKey: ["projects"] }); } });
  const update = useMutation({ mutationFn: projectsApi.update, onSuccess: () => { setEditing(null); queryClient.invalidateQueries({ queryKey: ["projects"] }); } });
  const archive = useMutation({ mutationFn: ({ id, archived }: { id: number; archived: boolean }) => projectsApi.archive(id, archived), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] }) });
  const impact = useQuery({ queryKey: ["project-impact", deleteTarget?.id], queryFn: () => projectsApi.impact(deleteTarget!.id), enabled: Boolean(deleteTarget) });
  const remove = useMutation({ mutationFn: ({ id, confirmName }: { id: number; confirmName: string }) => projectsApi.remove(id, confirmName), onSuccess: () => { setDeleteTarget(null); queryClient.invalidateQueries({ queryKey: ["projects"] }); } });
  return (
    <main className="page" data-dirty={editing || draftDirty ? "true" : undefined}>
      <PageTitle title="项目" description="维护客户、品牌、品类和竞品边界。" />
      <Panel title="新增项目">
        <form className="form-grid compact" onSubmit={(event) => { event.preventDefault(); create.mutate(draft); }}>
          {(["client_name", "brand_name", "product_category", "target_region", "competitors"] as const).map((key) => <label key={key}>{projectLabels[key]}<input value={draft[key]} onChange={(event) => { setDraftDirty(true); setDraft({ ...draft, [key]: event.target.value }); }} /></label>)}
          <button type="submit">保存项目</button>
        </form>
      </Panel>
      <Panel title="项目列表">
        <div className="list-toolbar"><label className="checkbox-field"><input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />显示已归档项目</label><span>{(projects.data?.projects || []).filter((project) => !project.archived_at).length} 个进行中</span></div>
        <AsyncBoundary loading={projects.isLoading} refreshing={projects.isFetching && !projects.isLoading} stale={projects.isError && Boolean(projects.data)} error={projects.data ? null : projects.error} empty={!projects.data?.projects.length} emptyLabel="还没有项目，先创建第一个客户项目。" onRetry={() => projects.refetch()}>
        <div className="data-table">
          <table><thead><tr><th>客户 / 品牌</th><th>状态</th><th>品类</th><th>地区</th><th>数据</th><th>操作</th></tr></thead><tbody>
            {projects.data?.projects.filter((project) => showArchived || !project.archived_at).map((project) => <tr key={project.id}><td><strong>{project.client_name}</strong><span>{project.brand_name}</span></td><td><StatusBadge status={project.archived_at ? "archived" : "active"} /></td><td>{project.product_category || "-"}</td><td>{project.target_region || "-"}</td><td>{project.question_count || 0} 问题 / {project.run_count || 0} 结果</td><td><div className="inline-actions"><button className="ghost" onClick={() => setEditing({ ...project })}><Edit3 size={14} />编辑</button><button className="ghost" disabled={archive.isPending} onClick={() => archive.mutate({ id: project.id, archived: !project.archived_at })}>{project.archived_at ? <RotateCcw size={14} /> : <Archive size={14} />}{project.archived_at ? "恢复" : "归档"}</button><button className="ghost danger-link" onClick={() => setDeleteTarget(project)}>删除</button></div></td></tr>)}
          </tbody></table>
        </div>
        </AsyncBoundary>
        {create.error ? <div className="error-box">{create.error.message}</div> : null}{archive.error ? <div className="error-box">{archive.error.message}</div> : null}
      </Panel>
      {editing ? <Modal title={`编辑项目：${editing.brand_name}`} onClose={() => setEditing(null)}><form className="form-grid" onSubmit={(event) => { event.preventDefault(); update.mutate(editing); }}>{(["client_name", "brand_name", "product_category", "target_region", "competitors"] as const).map((key) => <label key={key}>{projectLabels[key]}<input value={editing[key] || ""} onChange={(event) => setEditing({ ...editing, [key]: event.target.value })} /></label>)}<label>公司全称<input value={editing.company_name || ""} onChange={(event) => setEditing({ ...editing, company_name: event.target.value })} /></label><label>官网域名<input value={editing.website_domain || ""} onChange={(event) => setEditing({ ...editing, website_domain: event.target.value })} /></label><label className="wide">备注<textarea value={editing.notes || ""} onChange={(event) => setEditing({ ...editing, notes: event.target.value })} /></label><div className="inline-actions wide"><button type="submit" disabled={update.isPending}>{update.isPending ? "正在保存" : "保存修改"}</button><button className="ghost" type="button" onClick={() => setEditing(null)}>取消</button></div></form>{update.error ? <div className="error-box">{update.error.message}</div> : null}</Modal> : null}
      <ConfirmDialog open={Boolean(deleteTarget)} title="永久删除项目？" danger disabled={impact.isLoading || impact.isError || remove.isPending} confirmLabel={impact.isLoading ? "正在读取影响范围…" : remove.isPending ? "正在删除…" : "永久删除"} requireText={deleteTarget?.client_name} onClose={() => setDeleteTarget(null)} onConfirm={() => deleteTarget && remove.mutate({ id: deleteTarget.id, confirmName: deleteTarget.client_name })} description={<><p>这会级联删除该项目的问题、批次、运行记录与评估数据，操作不可恢复。</p>{impact.isError ? <div className="error-box">影响范围读取失败，暂不能删除。请关闭后重试。</div> : null}<dl className="delete-impact"><dt>客户</dt><dd>{deleteTarget?.client_name}</dd><dt>品牌</dt><dd>{deleteTarget?.brand_name}</dd><dt>问题</dt><dd>{impact.isLoading ? "读取中" : impact.data?.impact.question_count ?? "-"}</dd><dt>批次</dt><dd>{impact.data?.impact.batch_count ?? "-"}</dd><dt>运行记录</dt><dd>{impact.data?.impact.run_count ?? "-"}</dd><dt>评估</dt><dd>{impact.data?.impact.evaluation_count ?? "-"}</dd></dl></>} />
    </main>
  );
}

const projectLabels = { client_name: "客户名称", brand_name: "品牌名称", product_category: "产品品类", target_region: "目标地区", competitors: "竞品列表" };

export function QuestionsPage() {
  const { projectId } = useSelectionStore();
  const [searchParams, setSearchParams] = useSearchParams();
  const questions = useQuery({ queryKey: ["questions", projectId], queryFn: () => questionsApi.list(projectId), enabled: Boolean(projectId) });
  const [csvText, setCsvText] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [filePreviewText, setFilePreviewText] = useState("");
  const [xlsxPreflight, setXlsxPreflight] = useState<ImportPreflightResult | null>(null);
  const existingQuestions = useMemo(() => new Set((questions.data?.questions || []).map((item) => normalizeQuestion(item.question))), [questions.data?.questions]);
  const pastePreflight = useMemo(() => analyzeQuestionImport(csvText, existingQuestions), [csvText, existingQuestions]);
  const textFilePreflight = useMemo(() => analyzeQuestionImport(filePreviewText, existingQuestions), [filePreviewText, existingQuestions]);
  const filePreflight = filePreviewText ? textFilePreflight : xlsxPreflight;
  const pendingImportLabel = selectedFile ? filePreflight ? filePreflight.validRows.length : "待服务端校验" : pastePreflight.validRows.length;
  const previewFile = useMutation({
    mutationFn: async (file: File) => questionsApi.previewFile(projectId!, file.name, await fileToBase64(file)),
    onSuccess: (result) => setXlsxPreflight({
      validRows: result.valid_rows,
      valid: result.valid,
      duplicate: result.duplicate,
      empty: result.empty,
      invalid: result.invalid,
      reasons: result.issues.slice(0, 20).map((item) => `第 ${item.row} 行：${item.reason}`),
    }),
  });
  const importPaste = useMutation({
    mutationFn: () => questionsApi.importRows(projectId!, pastePreflight.validRows),
    onSuccess: () => {
      setCsvText("");
      queryClient.invalidateQueries({ queryKey: ["questions", projectId] });
    }
  });
  const importFile = useMutation({
    mutationFn: async () => {
      if (!selectedFile || !filePreflight) return { count: 0 };
      return questionsApi.importRows(projectId!, filePreflight.validRows);
    },
    onSuccess: () => {
      setSelectedFile(null);
      setFilePreviewText("");
      setXlsxPreflight(null);
      queryClient.invalidateQueries({ queryKey: ["questions", projectId] });
    }
  });
  const remove = useMutation({ mutationFn: questionsApi.remove, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["questions", projectId] }) });
  const importing = importPaste.isPending || importFile.isPending;
  const allQuestions = questions.data?.questions || [];
  const questionQuery = searchParams.get("questions_query") || "";
  const questionPlatform = searchParams.get("questions_platform") || "";
  const questionType = searchParams.get("questions_type") || "";
  const questionPlatforms = uniqueValues(allQuestions.flatMap((item) => splitFilterValues(item.suggested_platforms)));
  const questionTypes = uniqueValues(allQuestions.map((item) => item.question_type));
  const filteredQuestions = allQuestions.filter((item) => {
    const query = questionQuery.trim().toLocaleLowerCase();
    const matchesQuery = !query || [item.question_id, item.question, item.question_type, item.product_line, item.product_category, item.scenario]
      .some((value) => String(value || "").toLocaleLowerCase().includes(query));
    return matchesQuery
      && (!questionPlatform || splitFilterValues(item.suggested_platforms).includes(questionPlatform))
      && (!questionType || item.question_type === questionType);
  });
  const questionPage = pageFromSearchParams(searchParams, "questions_page", filteredQuestions.length);
  const visibleQuestions = pageItems(filteredQuestions, questionPage);
  const setQuestionPage = (page: number) => updatePageSearchParam(searchParams, setSearchParams, "questions_page", page);
  const setQuestionFilter = (key: string, value: string) => updateFilterSearchParam(searchParams, setSearchParams, key, value, "questions_page");
  return (
    <main className="page" data-dirty={csvText.trim() || selectedFile ? "true" : undefined}>
      <PageTitle title="问题库" description="导入、查看和维护当前项目的采样问题。" />
      <section className="question-workbench">
        <Panel title="导入问题">
          <div className="question-import">
            <div className="import-summary">
              <Metric label="当前问题" value={questions.data?.questions.length || 0} />
              <Metric label="待导入" value={pendingImportLabel} hint={selectedFile ? selectedFile.name : "采样只使用“问题内容”列"} />
            </div>
            <label>
              粘贴问题或表格
              <textarea
                className="question-textarea"
                placeholder={"粘贴带表头的 CSV/TSV，必须包含“问题内容”列。采样只使用“问题内容”，其他模板字段会在导出时保留。\n\n问题ID,问题内容,问题类型,产品线,平台,回答原文,品牌名是否出现,品牌名出现名称,推荐排名,是否Top3,官网域名类型,竞品出现,引用来源,原始数据,测试时间\nQ001,汽车白车身多材料连接有哪些推荐品牌？,品牌推荐,FDS,ChatGPT,,,,,,,,,,"}
                value={csvText}
                onChange={(event) => setCsvText(event.target.value)}
              />
            </label>
            <label className="file-import">
              <span>表格文件</span>
              <input
                type="file"
                accept=".xlsx,.csv,.tsv,.txt,text/csv,text/tab-separated-values,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={async (event) => {
                  const file = event.target.files?.[0] || null;
                  setSelectedFile(file);
                  setXlsxPreflight(null);
                  const isXlsx = Boolean(file?.name.toLowerCase().endsWith(".xlsx"));
                  setFilePreviewText(file && !isXlsx ? await file.text() : "");
                  if (file && isXlsx && projectId) previewFile.mutate(file);
                }}
              />
            </label>
            {(csvText || selectedFile) ? <ImportPreflight title={selectedFile ? selectedFile.name : "粘贴内容"} result={selectedFile ? filePreflight : pastePreflight} /> : null}
            <div className="import-actions">
              <button disabled={!projectId || !pastePreflight.validRows.length || importing} onClick={() => importPaste.mutate()}><Upload size={15} />{importPaste.isPending ? "正在导入" : `导入 ${pastePreflight.validRows.length} 个合法问题`}</button>
              <button className="ghost" type="button" disabled={!projectId || !selectedFile || !filePreflight?.validRows.length || previewFile.isPending || importing} onClick={() => importFile.mutate()}><FileSpreadsheet size={15} />{importFile.isPending ? "正在导入" : previewFile.isPending ? "正在预检" : `导入 ${filePreflight?.validRows.length || 0} 个合法问题`}</button>
              <button className="ghost" type="button" disabled={(!csvText && !selectedFile) || importing} onClick={() => { setCsvText(""); setSelectedFile(null); setFilePreviewText(""); setXlsxPreflight(null); }}>清空</button>
            </div>
            {importPaste.data ? <div className="success-box">已导入 {importPaste.data.count} 个问题</div> : null}
            {importFile.data ? <div className="success-box">已导入 {importFile.data.count} 个问题</div> : null}
            {importPaste.error ? <div className="error-box">{importPaste.error.message}</div> : null}
            {importFile.error ? <div className="error-box">{importFile.error.message}</div> : null}
            {previewFile.error ? <div className="error-box">{previewFile.error.message}</div> : null}
          </div>
        </Panel>
      </section>
      <Panel title={`问题列表 ${filteredQuestions.length}${filteredQuestions.length !== allQuestions.length ? ` / ${allQuestions.length}` : ""}`}>
        <TableFilters>
          <label>问题搜索<input type="search" value={questionQuery} placeholder="问题内容、ID、产品线…" onChange={(event) => setQuestionFilter("questions_query", event.target.value)} /></label>
          <label>平台<select value={questionPlatform} onChange={(event) => setQuestionFilter("questions_platform", event.target.value)}><option value="">全部平台</option>{questionPlatforms.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>问题类型<select value={questionType} onChange={(event) => setQuestionFilter("questions_type", event.target.value)}><option value="">全部类型</option>{questionTypes.map((value) => <option key={value}>{value}</option>)}</select></label>
          {(questionQuery || questionPlatform || questionType) ? <button type="button" className="ghost" onClick={() => clearSearchParams(searchParams, setSearchParams, ["questions_query", "questions_platform", "questions_type", "questions_page"])}>清除筛选</button> : null}
        </TableFilters>
        <AsyncBoundary loading={questions.isLoading} refreshing={questions.isFetching && !questions.isLoading} stale={questions.isError && Boolean(questions.data)} error={questions.data ? null : questions.error} empty={!allQuestions.length} emptyLabel="当前项目还没有问题" onRetry={() => questions.refetch()}>{filteredQuestions.length ? <><QuestionTable questions={visibleQuestions} onDelete={(id) => remove.mutate(id)} deletingId={remove.variables} /><Pagination page={questionPage} totalItems={filteredQuestions.length} onChange={setQuestionPage} /></> : <EmptyState title="没有符合筛选条件的问题" />}</AsyncBoundary>
        {remove.error ? <div className="error-box">{remove.error.message}</div> : null}
      </Panel>
    </main>
  );
}

const questionContentHeaders = new Set(["问题内容"]);

type ImportPreflightResult = { validRows: Record<string, string>[]; valid: number; empty: number; duplicate: number; invalid: number; reasons: string[] };

export function analyzeQuestionImport(text: string, existing: Set<string>): ImportPreflightResult {
  if (!text.trim()) return { validRows: [], valid: 0, empty: 0, duplicate: 0, invalid: 0, reasons: [] };
  const lines = splitLines(text);
  const first = lines.find((line) => line.trim()) || "";
  const delimiter = first.includes("\t") ? "\t" : first.includes(",") ? "," : "";
  if (!delimiter) return analyzePlainQuestionLines(lines, existing);
  const table = parseDelimitedRows(text, delimiter);
  const headers = (table[0] || []).map((cell) => cell.replace(/^\ufeff/, "").trim());
  const questionIndex = headers.findIndex((cell) => questionContentHeaders.has(normalizeHeader(cell)));
  if (questionIndex < 0) return { validRows: [], valid: 0, empty: 0, duplicate: 0, invalid: Math.max(table.length - 1, 1), reasons: ["缺少“问题内容”列"] };
  const seen = new Set<string>();
  const validRows: Record<string, string>[] = [];
  let empty = 0;
  let duplicate = 0;
  for (const cells of table.slice(1)) {
    const value = (cells[questionIndex] || "").trim();
    if (!value) { empty += 1; continue; }
    const normalized = normalizeQuestion(value);
    if (existing.has(normalized) || seen.has(normalized)) { duplicate += 1; continue; }
    seen.add(normalized);
    const row: Record<string, string> = {};
    headers.forEach((header, index) => { if (header) row[header] = (cells[index] || "").trim(); });
    validRows.push(row);
  }
  const reasons = [empty ? `${empty} 行问题内容为空` : "", duplicate ? `${duplicate} 行与现有或本次问题重复` : ""].filter(Boolean);
  return { validRows, valid: validRows.length, empty, duplicate, invalid: 0, reasons };
}

function analyzePlainQuestionLines(lines: string[], existing: Set<string>): ImportPreflightResult {
  const seen = new Set<string>();
  const validRows: Record<string, string>[] = [];
  let empty = 0;
  let duplicate = 0;
  lines.forEach((line) => {
    const value = line.trim();
    if (!value) { empty += 1; return; }
    const normalized = normalizeQuestion(value);
    if (existing.has(normalized) || seen.has(normalized)) { duplicate += 1; return; }
    seen.add(normalized);
    validRows.push({ "问题内容": value });
  });
  const reasons = [empty ? `${empty} 行问题内容为空` : "", duplicate ? `${duplicate} 行与现有或本次问题重复` : ""].filter(Boolean);
  return { validRows, valid: validRows.length, empty, duplicate, invalid: 0, reasons };
}

function splitLines(text: string) { return text.split(/\r\n|\n|\r/); }

function normalizeQuestion(value: string) { return value.trim().replace(/\s+/g, " ").toLowerCase(); }

function ImportPreflight({ title, result }: { title: string; result: ImportPreflightResult | null }) {
  if (!result) return <div className="preflight-card"><strong>{title}</strong><p>XLSX 将在上传后由服务端校验表头与内容。</p></div>;
  const skipped = result.empty + result.duplicate + result.invalid;
  return <div className="preflight-card" aria-live="polite"><div><strong>导入预检 · {title}</strong><span>{result.valid ? "可以导入" : "需要修复"}</span></div><dl><dt>合法</dt><dd>{result.valid}</dd><dt>重复</dt><dd>{result.duplicate}</dd><dt>空内容</dt><dd>{result.empty}</dd><dt>将跳过</dt><dd>{skipped}</dd></dl>{result.reasons.length ? <ul>{result.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>未发现重复或空内容。</p>}</div>;
}

function extractQuestionContents(text: string) {
  const structuredRows = extractQuestionRows(text);
  if (structuredRows.length) return structuredRows.map((row) => questionValue(row)).filter(Boolean);
  return [];
}

function extractQuestionRows(text: string): Record<string, string>[] {
  const fallbackLines = splitLines(text).map((line) => line.trim()).filter(Boolean);
  const firstLine = fallbackLines[0] || "";
  const delimiter = firstLine.includes("\t") ? "\t" : firstLine.includes(",") ? "," : "";
  if (!delimiter) return [];
  const table = parseDelimitedRows(text, delimiter);
  if (table.length < 2) return [];
  const headers = table[0].map((cell) => cell.replace(/^\ufeff/, "").trim());
  const questionIndex = headers.findIndex((cell) => questionContentHeaders.has(normalizeHeader(cell)));
  if (questionIndex < 0) return [];
  return table.slice(1).map((cells) => {
    const row: Record<string, string> = {};
    headers.forEach((header, index) => {
      if (header) row[header] = (cells[index] || "").trim();
    });
    return row;
  }).filter((row) => questionValue(row));
}

function questionValue(row: Record<string, string>) {
  for (const [key, value] of Object.entries(row)) {
    if (questionContentHeaders.has(normalizeHeader(key)) && value.trim()) return value.trim();
  }
  return "";
}

function normalizeHeader(value: string) {
  return value.replace(/^\ufeff/, "").trim().toLowerCase();
}

function parseDelimitedRows(text: string, delimiter: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === "\"") {
      if (quoted && next === "\"") {
        cell += "\"";
        index += 1;
      } else {
        quoted = !quoted;
      }
      continue;
    }
    if (char === delimiter && !quoted) {
      row.push(cell);
      cell = "";
      continue;
    }
    if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(cell);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      cell = "";
      continue;
    }
    cell += char;
  }
  row.push(cell);
  if (row.some((value) => value.trim())) rows.push(row);
  return rows;
}

function fileToBase64(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",")[1] || "");
    reader.onerror = () => reject(new Error("文件读取失败"));
    reader.readAsDataURL(file);
  });
}

function QuestionTable({ questions, onDelete, deletingId }: { questions: Question[]; onDelete: (id: number) => void; deletingId?: number }) {
  if (!questions.length) return <EmptyState title="当前项目还没有问题" />;
  return (
    <div className="data-table question-table">
      <table>
        <thead>
          <tr>
            <th>问题ID</th>
            <th>问题内容</th>
            <th>问题类型</th>
            <th>产品线</th>
            <th>平台</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {questions.map((q) => (
            <tr key={q.id}>
              <td>{q.question_id || "-"}</td>
              <td className="question-content-cell">{q.question}</td>
              <td>{q.question_type || "-"}</td>
              <td>{q.product_line || q.product_category || "-"}</td>
              <td>{q.suggested_platforms || "-"}</td>
              <td>
                <button className="icon-button danger" type="button" disabled={deletingId === q.id} aria-label="删除问题" title="删除问题" onClick={() => onDelete(q.id)}>
                  <Trash2 size={15} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ModelsPage() {
  const models = useQuery({ queryKey: ["models"], queryFn: modelsApi.list });
  const bochaSearch = useQuery({ queryKey: ["settings", "bocha-search"], queryFn: settingsApi.bochaSearch });
  const [draft, setDraft] = useState<ModelFormState>(newModelDraft());
  const [bochaKey, setBochaKey] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const modelGroups = useMemo(() => splitModelsForManagement(models.data?.models || []), [models.data?.models]);
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
  const updateBochaSearch = useMutation({
    mutationFn: settingsApi.updateBochaSearch,
    onSuccess: () => {
      setBochaKey("");
      queryClient.invalidateQueries({ queryKey: ["settings", "bocha-search"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    }
  });
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
      <BochaSearchCard
        config={bochaSearch.data}
        apiKey={bochaKey}
        pending={updateBochaSearch.isPending}
        error={updateBochaSearch.error?.message || bochaSearch.error?.message}
        onApiKeyChange={setBochaKey}
        onSubmit={() => updateBochaSearch.mutate({ api_key: bochaKey })}
      />
      <AsyncBoundary loading={models.isLoading} refreshing={models.isFetching && !models.isLoading} stale={models.isError && Boolean(models.data)} error={models.data ? null : models.error} empty={!models.data?.models.length} emptyLabel="暂无模型配置" onRetry={() => models.refetch()}>
        <div className="model-sections">
          {modelGroups.current.length ? <div className="model-grid">{modelGroups.current.map((model) => <ModelCard key={model.id} model={model} onEdit={() => setEditing(model)} onTest={() => test.mutate({ id: model.id })} />)}</div> : null}
          {modelGroups.archived.length ? (
            <details className="archived-models">
              <summary><span>归档模型</span><span>{modelGroups.archived.length} 个历史模型</span></summary>
              <p>GPT、Gemini、DeepSeek 官网联网搜索和 MiniMax 默认收起；展开后仍可编辑或测试。</p>
              <div className="model-grid">{modelGroups.archived.map((model) => <ModelCard key={model.id} model={model} onEdit={() => setEditing(model)} onTest={() => test.mutate({ id: model.id })} />)}</div>
            </details>
          ) : null}
        </div>
      </AsyncBoundary>
      {test.data ? <pre className="result-box">{JSON.stringify(test.data, null, 2)}</pre> : null}
      {test.error ? <div className="error-box">{test.error.message.includes("真实模型调用默认关闭") ? "真实模型测试当前被后端安全开关拦截。需要本地验收真实调用时，用 ALLOW_LIVE_MODEL_CALLS=1 重启 python3 app.py。" : test.error.message}</div> : null}
    </main>
  );
}

function BochaSearchCard({ config, apiKey, pending, error, onApiKeyChange, onSubmit }: { config?: BochaSearchConfig; apiKey: string; pending: boolean; error?: string; onApiKeyChange: (value: string) => void; onSubmit: () => void }) {
  return (
    <section className="panel">
      <div className="panel-head-row">
        <div>
          <h2>博查搜索</h2>
          <p>DeepSeek 联网搜索统一使用的外部检索能力。</p>
        </div>
        <span className={config?.configured ? "key-ok" : "key-missing"}><KeyRound size={14} />{config?.configured ? config.api_key_masked || "已配置" : "未配置"}</span>
      </div>
      <form className="inline-form" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
        <label>API Key<input type="password" value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} placeholder={config?.configured ? "留空不修改" : "填入博查 API Key"} /></label>
        <button type="submit" disabled={pending || !apiKey.trim()}>{pending ? "正在保存" : "保存配置"}</button>
      </form>
      <dl className="compact-dl">
        <dt>环境变量</dt><dd>{config?.env_keys?.join(" / ") || "BOCHA_API_KEY / BOCHA_SEARCH_API_KEY"}</dd>
        <dt>参数路径</dt><dd>{config?.web_search_param_path || "-"}</dd>
        <dt>使用范围</dt><dd>{config?.used_by?.join("；") || "DeepSeek 联网搜索"}</dd>
      </dl>
      {error ? <div className="error-box">{error}</div> : null}
    </section>
  );
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  const titleId = useId();
  const panelRef = useRef<HTMLElement>(null);
  useDialogFocus(true, panelRef, onClose);
  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <section ref={panelRef} className="modal-panel" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="modal-header">
          <h2 id={titleId}>{title}</h2>
          <button className="ghost" type="button" onClick={onClose}>关闭</button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

function newModelDraft(): ModelFormState {
  return {
    provider: "openrouter_gpt",
    label: "ChatGPT",
    model: "openai/gpt-5.2",
    api_family: "OpenRouter Chat Completions",
    api_base: "https://openrouter.ai/api/v1",
    api_key: "",
    model_type: "chat",
    priority: 100,
    daily_limit: 0,
    supports_pure: true,
    supports_search: true,
    supports_citation: true,
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
  return <article className="model-card"><header><div><strong>{model.label}</strong><span>{model.provider} / {model.model}</span></div><span className={model.has_key ? "key-ok" : "key-missing"}><KeyRound size={14} />{model.has_key ? model.api_key_masked || "已配置" : "未配置"}</span></header><div className="tag-row">{model.supports_search ? <span>联网</span> : null}{model.supports_reasoning ? <span>思考</span> : null}{model.supports_citation ? <span>引用</span> : null}{model.active ? <span>启用</span> : <span>停用</span>}</div><dl className="model-card-details"><dt>temperature</dt><dd>{String(defaults.temperature ?? "模型默认")}</dd><dt>reasoning</dt><dd>{String(defaults.reasoning_effort ?? "模型默认")}</dd><dt>api_base</dt><dd>{model.api_base || "-"}</dd><dt>note</dt><dd>{String(defaults.defaults_note ?? "-")}</dd></dl><div className="inline-actions"><button className="ghost" onClick={onEdit}>编辑设置</button><button className="ghost" onClick={onTest}>测试</button></div></article>;
}

const ARCHIVED_MODEL_PROVIDERS = new Set(["deepseek_web", "gemini", "minimax"]);

export function splitModelsForManagement(models: ModelConfig[]) {
  const archived: ModelConfig[] = [];
  const current: ModelConfig[] = [];
  for (const model of models) {
    const identity = `${model.label} ${model.model}`.toLowerCase();
    const isOpenRouterModel = model.provider === "openrouter_gpt" || model.provider === "openrouter_gemini";
    const shouldArchive = ARCHIVED_MODEL_PROVIDERS.has(model.provider) || (!isOpenRouterModel && /\bgpt\b|gemini/.test(identity));
    (shouldArchive ? archived : current).push(model);
  }
  return { current, archived };
}

const SAMPLING_ARCHIVED_MODEL_PROVIDERS = new Set(["openai", "gemini", "deepseek_web"]);

export function splitModelsForSampling(models: ModelConfig[]) {
  const archived: ModelConfig[] = [];
  const current: ModelConfig[] = [];
  for (const model of models) {
    (SAMPLING_ARCHIVED_MODEL_PROVIDERS.has(model.provider) ? archived : current).push(model);
  }
  return { current, archived };
}

export function SamplingPage() {
  const { projectId } = useSelectionStore();
  const questions = useQuery({ queryKey: ["questions", projectId], queryFn: () => questionsApi.list(projectId), enabled: Boolean(projectId) });
  const models = useQuery({ queryKey: ["models"], queryFn: modelsApi.list });
  const batches = useQuery({ queryKey: ["batches", projectId], queryFn: () => batchesApi.list(projectId || "all"), enabled: Boolean(projectId), refetchInterval: 2500 });
  const [selected, setSelected] = useState<Record<number, { mode: SamplingMode; reasoning_enabled: boolean }>>({});
  const [repeatCount, setRepeatCount] = useState(1);
  const [activeBatch, setActiveBatch] = useState<string>("");
  const [batchName, setBatchName] = useState("");
  const [batchDescription, setBatchDescription] = useState("");
  const [batchPurpose, setBatchPurpose] = useState("常规监测");
  const [batchTags, setBatchTags] = useState("");
  const [confirmStart, setConfirmStart] = useState(false);
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
    mutationFn: () => runsApi.start({ project_id: projectId, repeat_count: repeatCount, models: runModels, batch_name: batchName.trim(), description: batchDescription.trim(), purpose: batchPurpose, tags: batchTags.split(/[,，;；]/).map((item) => item.trim()).filter(Boolean), client_request_id: crypto.randomUUID() }),
    onSuccess: (data) => { setConfirmStart(false); setActiveBatch(data.batch_id); queryClient.invalidateQueries({ queryKey: ["batches"] }); }
  });
  const preflight = useMutation({
    mutationFn: () => runsApi.preflight({ project_id: projectId, repeat_count: repeatCount, models: runModels }),
    onSuccess: (result) => { if (result.ready) setConfirmStart(true); }
  });
  const rerun = useMutation({ mutationFn: () => batchesApi.rerunFailed(activeBatch), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["progress", activeBatch] }) });
  const pause = useMutation({
    mutationFn: () => batchesApi.pause(activeBatch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["progress", activeBatch] });
      queryClient.invalidateQueries({ queryKey: ["batches"] });
    }
  });
  const resume = useMutation({
    mutationFn: () => batchesApi.resume(activeBatch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["progress", activeBatch] });
      queryClient.invalidateQueries({ queryKey: ["batches"] });
    }
  });
  const progress = useQuery({ queryKey: ["progress", activeBatch], queryFn: () => batchesApi.progress(activeBatch), enabled: Boolean(activeBatch), refetchInterval: (query) => isTerminalStatus(query.state.data?.status) ? false : 1200 });
  useEffect(() => {
    if (activeBatch || !batches.data?.batches.length) return;
    const running = batches.data.batches.find((batch) => !isTerminalStatus(batch.status));
    if (running) setActiveBatch(running.batch_id);
  }, [activeBatch, batches.data?.batches]);
  const samplingModelGroups = useMemo(
    () => splitModelsForSampling((models.data?.models || []).filter((model) => model.active)),
    [models.data?.models]
  );
  const searchTaskCount = runModels.filter((item) => item.search_enabled).length;
  const totalTasks = (questions.data?.questions.length || 0) * runModels.length * repeatCount;
  const activeStatus = progress.data?.status;
  const batchRunning = Boolean(activeBatch && !isTerminalStatus(activeStatus));
  const failedSources = (progress.data?.source_statuses || []).some((item) => item.failed > 0);
  const renderModelCard = (model: ModelConfig) => {
    const config = selected[model.id];
    const enabled = Boolean(config);
    const mode = config?.mode || "pure";
    const defaultMode: SamplingMode = model.supports_pure ? "pure" : "search";
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
                    ? { ...prev, [model.id]: { mode: defaultMode, reasoning_enabled: false } }
                    : (Object.fromEntries(Object.entries(prev).filter(([id]) => Number(id) !== model.id)) as typeof prev)
                )
              }
            />
            <strong>{model.label}</strong>
          </label>
          <span className={model.has_key ? "key-ok" : "key-missing"}>{model.has_key ? "Key 已配置" : "缺少 Key"}</span>
        </header>
        <p>测试平台：{model.test_platform || model.label}</p>
        <div className="sampling-mode-group">
          <button className={mode === "pure" ? "is-active" : ""} type="button" disabled={!enabled || !model.supports_pure} onClick={() => setSelected((prev) => ({ ...prev, [model.id]: { ...prev[model.id], mode: "pure" } }))}>本体</button>
          <button className={mode === "search" ? "is-active" : ""} type="button" disabled={!enabled || !model.supports_search} onClick={() => setSelected((prev) => ({ ...prev, [model.id]: { ...prev[model.id], mode: "search" } }))}>联网</button>
          <button className={mode === "compare" ? "is-active" : ""} type="button" disabled={!enabled || !model.supports_pure || !model.supports_search} onClick={() => setSelected((prev) => ({ ...prev, [model.id]: { ...prev[model.id], mode: "compare" } }))}>本体+联网</button>
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
          <dt>说明</dt><dd>{model.supports_pure && model.supports_search ? "支持联网采样与本体对照" : model.supports_search ? "仅支持联网采样" : "仅支持本体采样"}</dd>
        </dl>
      </article>
    );
  };
  return (
    <main className="page sampling-page" data-dirty={batchName.trim() || batchDescription.trim() || batchTags.trim() || Object.keys(selected).length || repeatCount !== 1 ? "true" : undefined}>
      <PageTitle title="采样" description="选择问题范围和模型矩阵，对比模型本体与联网搜索结果。" />
      <section className="sampling-grid">
        <Panel title="批次信息"><label>批次名称<input value={batchName} maxLength={80} onChange={(event) => setBatchName(event.target.value)} placeholder="例如：7 月品牌基线复测" /></label><label>用途<select value={batchPurpose} onChange={(event) => setBatchPurpose(event.target.value)}><option>常规监测</option><option>项目基线</option><option>优化复测</option><option>客户演示</option></select></label><label>标签（可选）<input value={batchTags} onChange={(event) => setBatchTags(event.target.value)} placeholder="客户版，基线" /></label><label>说明（可选）<textarea rows={2} value={batchDescription} onChange={(event) => setBatchDescription(event.target.value)} placeholder="记录本次采样背景或口径" /></label></Panel>
        <Panel title="任务估算"><Metric label="当前问题" value={questions.data?.questions.length || 0} hint={`重复 ${repeatCount} 次`} /><label>重复次数<input type="number" min={1} max={10} value={repeatCount} onChange={(event) => setRepeatCount(Number(event.target.value) || 1)} /></label><Metric label="已选模型" value={Object.keys(selected).length} /><Metric label="预计任务" value={totalTasks} hint={`${searchTaskCount} 个联网配置`} /><button disabled={!projectId || !batchName.trim() || !totalTasks || start.isPending || preflight.isPending || batchRunning} onClick={() => preflight.mutate()}><Play size={15} />{batchRunning ? "采样运行中" : preflight.isPending ? "正在检查就绪度" : "检查并启动"}</button>{preflight.data && !preflight.data.ready ? <div className="error-box"><strong>启动前还有阻断项</strong><ul>{preflight.data.blockers.map((item) => <li key={`${item.code}-${item.message}`}>{item.message}{item.fix_path ? <> · <Link to={`${item.fix_path}?project_id=${projectId}`}>去修复</Link></> : null}</li>)}</ul></div> : null}{preflight.error ? <div className="error-box">{preflight.error.message}</div> : null}{start.error ? <div className="error-box">{start.error.message}{start.error instanceof ApiError && start.error.code === "ACTIVE_BATCH_EXISTS" && typeof start.error.details?.batch_id === "string" ? <> · <Link to={`/batches/${start.error.details.batch_id}?project_id=${projectId}`}>查看现有批次</Link></> : null}</div> : null}</Panel>
        <Panel title="运行监测">
          {activeBatch ? (
            <div className="run-monitor" aria-live="polite">
              <div className="monitor-head">
                <div><span>当前批次</span><strong>{activeBatch}</strong></div>
                <StatusBadge status={progress.data?.status || "queued"} />
              </div>
              <ProgressBar batch={progress.data} />
              {progress.isError ? <div className="error-box">{progress.error.message}</div> : null}
              <SourceStatusList rows={progress.data?.source_statuses || []} compact />
              {rerun.error ? <div className="error-box">{rerun.error.message}</div> : null}
              {pause.error ? <div className="error-box">{pause.error.message}</div> : null}
              {resume.error ? <div className="error-box">{resume.error.message}</div> : null}
              <div className="inline-actions">
                <Link className="button ghost" to={`/batches/${activeBatch}`}>{failedSources ? "查看失败原因" : "查看批次详情"}</Link>
                {isPausableStatus(progress.data?.status) ? <button className="ghost" type="button" disabled={pause.isPending} onClick={() => pause.mutate()}><Pause size={15} />{pause.isPending ? "正在暂停" : "暂停"}</button> : null}
                {isResumableStatus(progress.data?.status) ? <button className="ghost" type="button" disabled={resume.isPending} onClick={() => resume.mutate()}><Play size={15} />{resumeActionLabel(progress.data?.status, resume.isPending)}</button> : null}
                {failedSources && isRerunnableStatus(progress.data?.status) ? <button className="ghost" type="button" disabled={rerun.isPending} onClick={() => rerun.mutate()}>{rerun.isPending ? "正在重跑" : "重跑失败"}</button> : null}
              </div>
            </div>
          ) : <EmptyState title="尚未启动采样" />}
        </Panel>
      </section>
      <Panel title="模型矩阵">
        {samplingModelGroups.current.length ? <div className="model-matrix">{samplingModelGroups.current.map(renderModelCard)}</div> : null}
        {samplingModelGroups.archived.length ? (
          <details className="archived-models sampling-archived-models">
            <summary><span>归档模型</span><span>{samplingModelGroups.archived.length} 个历史配置</span></summary>
            <p>GPT、Gemini 和 DeepSeek 官网联网搜索默认收起；展开后仍可选入采样。</p>
            <div className="model-matrix">{samplingModelGroups.archived.map(renderModelCard)}</div>
          </details>
        ) : null}
      </Panel>
      <ConfirmDialog open={confirmStart} title="确认启动采样" confirmLabel={start.isPending ? "正在创建…" : `启动 ${totalTasks} 个任务`} onClose={() => setConfirmStart(false)} onConfirm={() => start.mutate()} description={<dl className="start-summary"><dt>批次</dt><dd>{batchName || "未命名"}</dd><dt>用途</dt><dd>{batchPurpose}</dd><dt>问题</dt><dd>{questions.data?.questions.length || 0} 个</dd><dt>模型配置</dt><dd>{runModels.length} 个</dd><dt>重复</dt><dd>{repeatCount} 次</dd><dt>总任务</dt><dd><strong>{totalTasks}</strong> 个</dd><dt>预计耗时</dt><dd>暂不可估算</dd></dl>} />
    </main>
  );
}

export function BatchesPage() {
  const { projectId } = useSelectionStore();
  const [searchParams, setSearchParams] = useSearchParams();
  const batches = useQuery({ queryKey: ["batches", projectId], queryFn: () => batchesApi.list(projectId || "all"), refetchInterval: 2500 });
  const allBatches = batches.data?.batches || [];
  const batchPage = pageFromSearchParams(searchParams, "batches_page", allBatches.length);
  const visibleBatches = pageItems(allBatches, batchPage);
  return <main className="page"><PageTitle title="批次" description="集中查看后台采样任务、状态和导出入口。" /><Panel title={`批次列表 ${allBatches.length}`}><AsyncBoundary loading={batches.isLoading} refreshing={batches.isFetching && !batches.isLoading} stale={batches.isError && Boolean(batches.data)} error={batches.data ? null : batches.error} empty={!allBatches.length} emptyLabel="当前项目还没有采样批次。" onRetry={() => batches.refetch()}><BatchTable batches={visibleBatches} /><Pagination page={batchPage} totalItems={allBatches.length} onChange={(page) => updatePageSearchParam(searchParams, setSearchParams, "batches_page", page)} /></AsyncBoundary></Panel></main>;
}

const PAGE_SIZE = 10;

function pageFromSearchParams(searchParams: URLSearchParams, key: string, totalItems: number) {
  const requested = Number.parseInt(searchParams.get(key) || "1", 10);
  const page = Number.isFinite(requested) && requested > 0 ? requested : 1;
  return Math.min(page, Math.max(1, Math.ceil(totalItems / PAGE_SIZE)));
}

function pageItems<T>(items: T[], page: number) {
  const start = (page - 1) * PAGE_SIZE;
  return items.slice(start, start + PAGE_SIZE);
}

function updatePageSearchParam(searchParams: URLSearchParams, setSearchParams: ReturnType<typeof useSearchParams>[1], key: string, page: number) {
  const next = new URLSearchParams(searchParams);
  if (page <= 1) next.delete(key);
  else next.set(key, String(page));
  setSearchParams(next, { replace: true });
}

function updateFilterSearchParam(searchParams: URLSearchParams, setSearchParams: ReturnType<typeof useSearchParams>[1], key: string, value: string, pageKey: string) {
  const next = new URLSearchParams(searchParams);
  if (value) next.set(key, value); else next.delete(key);
  next.delete(pageKey);
  setSearchParams(next, { replace: true });
}

function clearSearchParams(searchParams: URLSearchParams, setSearchParams: ReturnType<typeof useSearchParams>[1], keys: string[]) {
  const next = new URLSearchParams(searchParams);
  keys.forEach((key) => next.delete(key));
  setSearchParams(next, { replace: true });
}

function uniqueValues(values: Array<string | undefined>) {
  return Array.from(new Set(values.map((value) => String(value || "").trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function splitFilterValues(value?: string) {
  return String(value || "").split(/[,，;；|/]/).map((item) => item.trim()).filter(Boolean);
}

function TableFilters({ children }: { children: ReactNode }) {
  return <div className="table-filters" aria-label="表格筛选">{children}</div>;
}

export function BatchDetailPage() {
  const { batchId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { projectId, setProjectId } = useSelectionStore();
  const toast = useToast();
  const [selectedFailed, setSelectedFailed] = useState<string[]>([]);
  const [retrySource, setRetrySource] = useState("");
  const [reviewing, setReviewing] = useState<ModelRun | null>(null);
  const batch = useQuery({ queryKey: ["batch", batchId], queryFn: () => batchesApi.get(batchId), refetchInterval: 2500 });
  useEffect(() => {
    const ownerProjectId = batch.data?.batch.project_id;
    if (!ownerProjectId) return;
    if (ownerProjectId !== projectId) setProjectId(ownerProjectId);
    if (searchParams.get("project_id") !== String(ownerProjectId)) {
      const next = new URLSearchParams(searchParams);
      next.set("project_id", String(ownerProjectId));
      setSearchParams(next, { replace: true });
    }
  }, [batch.data?.batch.project_id, projectId, searchParams, setProjectId, setSearchParams]);
  const progress = useQuery({ queryKey: ["progress", batchId], queryFn: () => batchesApi.progress(batchId), enabled: Boolean(batchId), refetchInterval: (query) => isTerminalStatus(query.state.data?.status) ? false : 1200 });
  const runs = useQuery({ queryKey: ["batch-runs", batchId], queryFn: () => batchesApi.runs(batchId), refetchInterval: 3000 });
  const attempts = useQuery({ queryKey: ["batch-attempts", batchId], queryFn: () => batchesApi.attempts(batchId), enabled: Boolean(batchId) });
  const retry = useMutation({
    mutationFn: (payload: { scope: "all" | "source" | "tasks"; source?: string; task_ids?: string[] }) => batchesApi.retry(batchId, payload),
    onSuccess: () => {
      toast("已提交重试任务");
      setSelectedFailed([]);
      queryClient.invalidateQueries({ queryKey: ["batch", batchId] });
      queryClient.invalidateQueries({ queryKey: ["progress", batchId] });
      queryClient.invalidateQueries({ queryKey: ["batch-runs", batchId] });
      queryClient.invalidateQueries({ queryKey: ["batch-attempts", batchId] });
    }
  });
  const pause = useMutation({
    mutationFn: () => batchesApi.pause(batchId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batch", batchId] });
      queryClient.invalidateQueries({ queryKey: ["progress", batchId] });
      queryClient.invalidateQueries({ queryKey: ["batches"] });
    }
  });
  const resume = useMutation({
    mutationFn: () => batchesApi.resume(batchId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batch", batchId] });
      queryClient.invalidateQueries({ queryKey: ["progress", batchId] });
      queryClient.invalidateQueries({ queryKey: ["batch-runs", batchId] });
      queryClient.invalidateQueries({ queryKey: ["batches"] });
    }
  });
  const archiveBatch = useMutation({
    mutationFn: (archived: boolean) => batchesApi.archive(batchId, archived),
    onSuccess: () => {
      toast("批次归档状态已更新");
      queryClient.invalidateQueries({ queryKey: ["batch", batchId] });
      queryClient.invalidateQueries({ queryKey: ["batches"] });
    }
  });
  const rows = runs.data?.runs || [];
  const failedRows = rows.filter((row) => row.status === "failed");
  const attemptRows = attempts.data?.attempts || [];
  const failedPage = pageFromSearchParams(searchParams, "failed_page", failedRows.length);
  const resultQuery = searchParams.get("results_query") || "";
  const resultPlatform = searchParams.get("results_platform") || "";
  const resultStatus = searchParams.get("results_status") || "";
  const resultPlatforms = uniqueValues(rows.map(runPlatform));
  const resultStatuses = uniqueValues(rows.map((row) => row.status));
  const filteredRows = rows.filter((row) => {
    const query = resultQuery.trim().toLocaleLowerCase();
    const matchesQuery = !query || [row.source_question_id, row.question, row.question_type, row.product_line]
      .some((value) => String(value || "").toLocaleLowerCase().includes(query));
    return matchesQuery && (!resultPlatform || runPlatform(row) === resultPlatform) && (!resultStatus || row.status === resultStatus);
  });
  const resultsPage = pageFromSearchParams(searchParams, "results_page", filteredRows.length);
  const attemptsPage = pageFromSearchParams(searchParams, "attempts_page", attemptRows.length);
  const visibleFailedRows = pageItems(failedRows, failedPage);
  const visibleRows = pageItems(filteredRows, resultsPage);
  const visibleAttempts = pageItems(attemptRows, attemptsPage);
  const focusedRunId = searchParams.get("run_id") || "";
  const failureGroups = useMemo(() => groupFailures(failedRows), [failedRows]);
  const sources = Array.from(new Set(failedRows.map(runPlatform)));
  const batchStatus = progress.data?.status || batch.data?.batch.status;
  const hasFailures = (progress.data?.source_statuses || []).some((item) => item.failed > 0) || rows.some((row) => row.status === "failed");
  const canResume = isResumableStatus(batchStatus) && (batchStatus !== "failed" || hasIncompleteWork(progress.data || batch.data?.batch));
  useEffect(() => {
    if (!focusedRunId || !rows.length) return;
    const index = rows.findIndex((row) => String(row.run_id || row.id) === focusedRunId);
    if (index < 0) return;
    const targetPage = Math.floor(index / PAGE_SIZE) + 1;
    if (targetPage !== resultsPage) {
      updatePageSearchParam(searchParams, setSearchParams, "results_page", targetPage);
      return;
    }
    window.setTimeout(() => document.getElementById(`run-${focusedRunId}`)?.scrollIntoView({ block: "center" }), 0);
  }, [focusedRunId, resultsPage, rows, searchParams, setSearchParams]);
  return <main className="page"><PageTitle title={batch.data?.batch.batch_name || `批次 ${batchId}`} description={`${batchId} · 查看进度、失败分类、尝试历史和数据质检。`} action={<div className="inline-actions"><a className="button ghost" href={apiPath(`/api/export/batches/${batchId}/runs.xls`)} target="_blank" rel="noreferrer"><FileDown size={15} />导出明细</a>{isPausableStatus(batchStatus) ? <button className="ghost" type="button" onClick={() => pause.mutate()} disabled={pause.isPending}><Pause size={15} />{pause.isPending ? "正在暂停" : "暂停"}</button> : null}{canResume ? <button className="ghost" type="button" onClick={() => resume.mutate()} disabled={resume.isPending}><Play size={15} />{resumeActionLabel(batchStatus, resume.isPending)}</button> : null}{isTerminalStatus(batchStatus) ? <button className="ghost" type="button" onClick={() => archiveBatch.mutate(!batch.data?.batch.archived_at)} disabled={archiveBatch.isPending}>{batch.data?.batch.archived_at ? <RotateCcw size={15} /> : <Archive size={15} />}{batch.data?.batch.archived_at ? "恢复批次" : "归档批次"}</button> : null}</div>} />
    <AsyncBoundary loading={batch.isLoading} error={batch.error} onRetry={() => batch.refetch()}>
      <Panel title="进度">{batch.data?.batch ? <div className="batch-overview"><StatusBadge status={batchStatus || batch.data.batch.status} /><ProgressBar batch={progress.data || batch.data.batch}/><dl><dt>用途</dt><dd>{batch.data.batch.purpose || "-"}</dd><dt>说明</dt><dd>{batch.data.batch.description || "-"}</dd><dt>结果口径</dt><dd>{batch.data.batch.outcome || "pending"}</dd></dl></div> : null}{(progress.data?.error || batch.data?.batch.error) && batchStatus === "failed" ? <div className="error-box">{progress.data?.error || batch.data?.batch.error}</div> : null}{pause.error ? <div className="error-box">{pause.error.message}</div> : null}{resume.error ? <div className="error-box">{resume.error.message}</div> : null}{archiveBatch.error ? <div className="error-box">{archiveBatch.error.message}</div> : null}</Panel>
    </AsyncBoundary>
    <section className="two-column"><Panel title="测试平台摘要">{progress.isError ? <div className="error-box">{progress.error.message}</div> : null}<SourceStatusList rows={progress.data?.source_statuses || []} /></Panel><Panel title="失败分类">{failureGroups.length ? <div className="failure-groups">{failureGroups.map((group) => <div key={group.category}><span>{errorCategoryLabel(group.category)}</span><strong>{group.count}</strong><em>{group.retryable ? "可重试" : "先修复配置"}</em></div>)}</div> : <EmptyState title="暂无失败任务" />}</Panel></section>
    <Panel title="不可变配置快照"><details><summary>查看本批次创建时的问题与模型口径</summary><pre className="config-snapshot">{JSON.stringify(batch.data?.batch.config_snapshot || {}, null, 2)}</pre></details></Panel>
    {hasFailures && isRerunnableStatus(batchStatus) ? <Panel title="分类重试"><div className="retry-toolbar"><button disabled={retry.isPending} onClick={() => retry.mutate({ scope: "all" })}>重试全部失败任务（{failedRows.length}）</button><select aria-label="选择重试信息源" value={retrySource} onChange={(event) => setRetrySource(event.target.value)}><option value="">按信息源选择</option>{sources.map((source) => <option key={source}>{source}</option>)}</select><button className="ghost" disabled={!retrySource || retry.isPending} onClick={() => retry.mutate({ scope: "source", source: retrySource })}>重试此信息源{retrySource ? `（${failedRows.filter((row) => runPlatform(row) === retrySource).length}）` : ""}</button><button className="ghost" disabled={!selectedFailed.length || retry.isPending} onClick={() => retry.mutate({ scope: "tasks", task_ids: selectedFailed })}>重试已选（{selectedFailed.length}）</button></div><div className="data-table dense"><table><thead><tr><th><span className="sr-only">选择</span></th><th>平台</th><th>问题</th><th>类别</th><th>错误</th></tr></thead><tbody>{visibleFailedRows.map((row) => { const retryId = row.task_id || row.run_id || String(row.id); return <tr key={row.id}><td><input aria-label={`选择失败任务 ${retryId}`} type="checkbox" checked={selectedFailed.includes(retryId)} onChange={(event) => setSelectedFailed((current) => event.target.checked ? [...current, retryId] : current.filter((id) => id !== retryId))} /></td><td>{runPlatform(row)}</td><td>{row.question || row.source_question_id || `任务 #${row.id}`}</td><td>{errorCategoryLabel(classifyRunError(row))}</td><td className="error-message-cell">{row.error_message || "-"}</td></tr>; })}</tbody></table></div><Pagination page={failedPage} totalItems={failedRows.length} onChange={(page) => updatePageSearchParam(searchParams, setSearchParams, "failed_page", page)} />{retry.error ? <div className="error-box">{retry.error.message}</div> : null}</Panel> : null}
    <Panel title={`当前结果与数据质检 ${filteredRows.length}${filteredRows.length !== rows.length ? ` / ${rows.length}` : ""}`}><TableFilters><label>问题搜索<input type="search" value={resultQuery} placeholder="问题内容、ID、类型…" onChange={(event) => updateFilterSearchParam(searchParams, setSearchParams, "results_query", event.target.value, "results_page")} /></label><label>平台<select value={resultPlatform} onChange={(event) => updateFilterSearchParam(searchParams, setSearchParams, "results_platform", event.target.value, "results_page")}><option value="">全部平台</option>{resultPlatforms.map((value) => <option key={value}>{value}</option>)}</select></label><label>状态<select value={resultStatus} onChange={(event) => updateFilterSearchParam(searchParams, setSearchParams, "results_status", event.target.value, "results_page")}><option value="">全部状态</option>{resultStatuses.map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></label>{(resultQuery || resultPlatform || resultStatus) ? <button type="button" className="ghost" onClick={() => clearSearchParams(searchParams, setSearchParams, ["results_query", "results_platform", "results_status", "results_page"])}>清除筛选</button> : null}</TableFilters><AsyncBoundary loading={runs.isLoading} refreshing={runs.isFetching && !runs.isLoading} stale={runs.isError && Boolean(runs.data)} error={runs.data ? null : runs.error} empty={!rows.length} emptyLabel="暂无运行明细" onRetry={() => runs.refetch()}>{filteredRows.length ? <><RunsTable runs={visibleRows} onReview={setReviewing} focusedRunId={focusedRunId} /><Pagination page={resultsPage} totalItems={filteredRows.length} onChange={(page) => updatePageSearchParam(searchParams, setSearchParams, "results_page", page)} /></> : <EmptyState title="没有符合筛选条件的采样结果" />}</AsyncBoundary></Panel>
    <Panel title="Attempt History"><AsyncBoundary loading={attempts.isLoading} refreshing={attempts.isFetching && !attempts.isLoading} stale={attempts.isError && Boolean(attempts.data)} error={attempts.data ? null : attempts.error} empty={!attemptRows.length} emptyLabel="暂无尝试历史" onRetry={() => attempts.refetch()}><AttemptHistory rows={visibleAttempts} /><Pagination page={attemptsPage} totalItems={attemptRows.length} onChange={(page) => updatePageSearchParam(searchParams, setSearchParams, "attempts_page", page)} /></AsyncBoundary></Panel>
    {reviewing ? <QualityReviewDialog run={reviewing} projectId={batch.data?.batch.project_id || projectId || 0} batchId={batchId} onClose={() => setReviewing(null)} /> : null}
  </main>;
}

function classifyRunError(run: ModelRun) {
  if (run.error_category || run.error_code) return String(run.error_category || run.error_code).toLowerCase();
  const message = String(run.error_message || "").toLowerCase();
  if (/401|unauthorized|api.?key|鉴权/.test(message)) return "auth";
  if (/429|rate.?limit|限流/.test(message)) return "rate_limit";
  if (/timeout|timed out|超时/.test(message)) return "timeout";
  if (/region|地区|区域|403/.test(message)) return "region";
  if (/json|parse|解析/.test(message)) return "parse";
  if (/dns|tls|network|connection|依赖/.test(message)) return "dependency";
  return "unknown";
}

function errorCategoryLabel(category: string) { return ({ auth: "鉴权失败", authentication: "鉴权失败", rate_limit: "限流", timeout: "超时", region: "区域限制", parse: "解析错误", malformed_response: "响应格式错误", upstream: "上游服务错误", dependency: "依赖故障", model_not_found: "模型不存在", unknown: "未知错误" } as Record<string, string>)[category] || category || "未知错误"; }

function groupFailures(rows: ModelRun[]) {
  const grouped = new Map<string, number>();
  rows.forEach((row) => { const category = classifyRunError(row); grouped.set(category, (grouped.get(category) || 0) + 1); });
  return Array.from(grouped, ([category, count]) => ({ category, count, retryable: !["auth", "authentication", "region", "model_not_found"].includes(category) }));
}

function AttemptHistory({ rows }: { rows: ModelRun[] }) {
  return <div className="data-table dense"><table><thead><tr><th>尝试</th><th>任务</th><th>实际来源 / 模型</th><th>模式</th><th>状态</th><th>耗时</th><th>时间</th><th>错误</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.attempt_id || row.run_id || `${row.id}-${index}`}><td>#{row.attempt_no || row.attempt_index || 1}{row.is_current ? <span>当前</span> : null}</td><td>{row.task_key || row.source_question_id || row.task_id || "-"}</td><td>{row.actual_provider || row.provider || row.test_platform || "-"}<span>{row.actual_model || row.model || "-"}</span></td><td>{row.mode || (row.search_enabled ? "search" : "pure")}</td><td><StatusBadge status={row.status} /></td><td>{row.latency_ms || 0} ms</td><td>{formatDateTime(row.finished_at || row.requested_at || row.started_at)}</td><td className="error-message-cell">{row.error_message || "-"}</td></tr>)}</tbody></table></div>;
}

function QualityReviewDialog({ run, projectId, batchId, onClose }: { run: ModelRun; projectId: number; batchId: string; onClose: () => void }) {
  const toast = useToast();
  const runId = String(run.run_id || run.id);
  const [decision, setDecision] = useState<QualityDecision>("valid");
  const [reason, setReason] = useState("");
  const history = useQuery({ queryKey: ["quality", runId], queryFn: () => qualityApi.listForRun(runId), retry: false });
  const mutation = useMutation({ mutationFn: () => qualityApi.review(runId, { decision, reason }), onSuccess: () => { toast("质检结论已保存"); queryClient.invalidateQueries({ queryKey: ["quality", projectId, batchId] }); queryClient.invalidateQueries({ queryKey: ["quality", runId] }); onClose(); } });
  const events = history.data?.review?.history || (history.data?.review ? [history.data.review] : []);
  return <Modal title="数据质检" onClose={onClose}><div className="review-context"><strong>{run.question || run.source_question_id || `运行 #${run.id}`}</strong><p>{run.response_text || run.error_message || "暂无回答内容"}</p></div>{history.isError ? <div className="feature-unavailable"><strong>历史质检记录暂不可用</strong><span>{history.error.message}</span></div> : <AsyncBoundary loading={history.isLoading}>{events.length ? <div className="review-history"><strong>质检历史</strong>{events.map((event, index) => <div key={`${event.created_at}-${index}`}><StatusBadge status={event.decision} /><span>{event.reason || "未填写理由"}</span><em>{event.actor || event.reviewer || "-"} · {formatDateTime(event.created_at)}</em></div>)}</div> : null}</AsyncBoundary>}<form className="review-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>结论<select value={decision} onChange={(event) => setDecision(event.target.value as QualityDecision)}><option value="valid">有效</option><option value="excluded">排除</option><option value="needs_review">需要复核</option></select></label><label>理由<textarea required value={reason} onChange={(event) => setReason(event.target.value)} placeholder="记录判断依据，便于后续审计" /></label><div className="inline-actions"><button disabled={!reason.trim() || mutation.isPending}>{mutation.isPending ? "正在保存" : "保存质检结论"}</button><button className="ghost" type="button" onClick={onClose}>取消</button></div>{mutation.error ? <div className="error-box">{mutation.error.message}</div> : null}</form></Modal>;
}

export function AnalysisPage() {
  const { projectId } = useSelectionStore();
  const toast = useToast();
  const [batchId, setBatchId] = useState("");
  const [baselineBatchId, setBaselineBatchId] = useState("");
  const [comparisonBatchId, setComparisonBatchId] = useState("");
  const [reportTitle, setReportTitle] = useState("");
  const batches = useQuery({ queryKey: ["batches", projectId], queryFn: () => batchesApi.list(projectId || "all"), enabled: Boolean(projectId), refetchInterval: 3000 });
  const summary = useQuery({
    queryKey: ["analytics-summary", projectId, batchId],
    queryFn: () => analyticsApi.summary(projectId!, batchId || undefined),
    enabled: Boolean(projectId)
  });
  const data = summary.data;
  const reports = useQuery({ queryKey: ["reports", projectId], queryFn: () => reportsApi.list(projectId!), enabled: Boolean(projectId), retry: false });
  const comparison = useQuery({ queryKey: ["batch-comparison", projectId, baselineBatchId, comparisonBatchId], queryFn: () => reportsApi.compare(projectId!, baselineBatchId, comparisonBatchId), enabled: Boolean(projectId && baselineBatchId && comparisonBatchId), retry: false });
  const createReport = useMutation({ mutationFn: () => reportsApi.create({ project_id: projectId!, batch_id: batchId || undefined, title: reportTitle.trim() || `${data?.meta.brand_name || "GEO"} 审计报告` }), onSuccess: () => { setReportTitle(""); toast("报告草稿已创建"); queryClient.invalidateQueries({ queryKey: ["reports", projectId] }); } });
  const transitionReport = useMutation({ mutationFn: ({ id, status }: { id: string; status: ReportStatus }) => status === "frozen" ? reportsApi.freeze(id) : reportsApi.transition(id, status), onSuccess: () => { toast("报告状态已更新"); queryClient.invalidateQueries({ queryKey: ["reports", projectId] }); } });
  const chartRows = data?.provider_breakdown.map((item) => ({ name: item.name, 命中率: item.mention_rate, Top3: item.top3_rate, 官网引用率: item.owned_citation_rate })) || [];
  const action = (
    <div className="inline-actions analysis-actions">
      <select aria-label="分析范围" value={batchId} onChange={(event) => setBatchId(event.target.value)}>
        <option value="">项目整体</option>
        {(batches.data?.batches || []).map((batch) => <option key={batch.batch_id} value={batch.batch_id}>{batch.batch_id} / {batch.status}</option>)}
      </select>
      <button className="ghost" type="button" onClick={() => summary.refetch()}><RefreshCw size={15} />刷新</button>
    </div>
  );
  if (!projectId) return <main className="page"><PageTitle title="分析" description="选择项目后查看 GEO 报告工作台。" /><EmptyState title="请先选择项目" /></main>;
  return (
    <main className="page analysis-page">
      <PageTitle title="分析" description="从项目或单批次视角查看品牌可见性、模型差异、竞品风险和引用结构。" action={action} />
      <AsyncBoundary loading={summary.isLoading} refreshing={summary.isFetching && !summary.isLoading} stale={summary.isError && Boolean(data)} error={data ? null : summary.error} empty={!summary.isLoading && !data} emptyLabel="当前项目暂无可分析数据" onRetry={() => summary.refetch()}>
        {data ? <AnalysisWorkspace summary={data} chartRows={chartRows} /> : null}
      </AsyncBoundary>
      <section className="two-column delivery-grid">
        <Panel title="报告版本">
          <div className="report-create"><input aria-label="报告标题" value={reportTitle} onChange={(event) => setReportTitle(event.target.value)} placeholder={`${data?.meta.brand_name || "品牌"} GEO 审计报告`} /><button disabled={!data || createReport.isPending} onClick={() => createReport.mutate()}>创建草稿</button></div>
          {reports.isError ? <div className="feature-unavailable"><strong>报告版本服务暂不可用</strong><span>{reports.error.message}</span></div> : <AsyncBoundary loading={reports.isLoading} empty={!reports.data?.reports.length} emptyLabel="暂无报告版本，先从当前分析创建草稿。"><div className="report-list">{reports.data?.reports.map((report) => <article key={report.report_id}><div><strong>{report.title}</strong><span>V{report.version_no} · {formatDateTime(report.created_at)}</span></div><StatusBadge status={report.status} /><div className="inline-actions">{report.status === "draft" ? <button className="ghost" disabled={transitionReport.isPending} onClick={() => transitionReport.mutate({ id: report.report_id, status: "reviewed" })}>提交审核</button> : null}{report.status === "reviewed" ? <button disabled={transitionReport.isPending} onClick={() => transitionReport.mutate({ id: report.report_id, status: "frozen" })}>冻结交付</button> : null}{report.status === "frozen" ? <button className="ghost" disabled={transitionReport.isPending} onClick={() => transitionReport.mutate({ id: report.report_id, status: "archived" })}>归档</button> : null}{["frozen", "archived"].includes(report.status) ? <><a className="button ghost" href={apiPath(`/api/reports/${report.report_id}/export`)} target="_blank" rel="noreferrer">客户版快照</a><a className="button ghost" href={apiPath(`/api/reports/${report.report_id}/export?include_attempts=1`)} target="_blank" rel="noreferrer">内部诊断版</a></> : null}</div></article>)}</div></AsyncBoundary>}
          {createReport.error ? <div className="error-box">{createReport.error.message}</div> : null}{transitionReport.error ? <div className="error-box">{transitionReport.error.message}</div> : null}
        </Panel>
        <Panel title="批次对比">
          <div className="compare-selectors"><label>基线批次<select value={baselineBatchId} onChange={(event) => setBaselineBatchId(event.target.value)}><option value="">请选择</option>{(batches.data?.batches || []).map((batch) => <option key={batch.batch_id} value={batch.batch_id}>{batch.batch_name || batch.batch_id}</option>)}</select></label><label>复测批次<select value={comparisonBatchId} onChange={(event) => setComparisonBatchId(event.target.value)}><option value="">请选择</option>{(batches.data?.batches || []).map((batch) => <option key={batch.batch_id} value={batch.batch_id}>{batch.batch_name || batch.batch_id}</option>)}</select></label></div>
          {!baselineBatchId || !comparisonBatchId ? <EmptyState title="选择基线与复测批次后查看变化" /> : comparison.isError ? <div className="feature-unavailable"><strong>批次对比暂不可用</strong><span>{comparison.error.message}</span></div> : <AsyncBoundary loading={comparison.isLoading}><div className="comparison-result"><div className={comparison.data?.comparable ? "compare-verdict is-ok" : "compare-verdict is-warning"}>{comparison.data?.comparable ? "口径一致，可以直接对比" : "口径不完全一致，请谨慎解读"}</div>{comparison.data?.comparability_reasons.map((reason) => <p key={reason.code}>{reason.message}</p>)}{comparison.data?.delta_is_directional_only ? <p>因配置口径不同，变化值仅供方向性参考。</p> : null}<div className="comparison-metrics">{Object.entries(comparison.data?.delta || {}).map(([label, delta]) => <div key={label}><span>{comparisonMetricLabel(label)}</span><strong>{delta > 0 ? "+" : ""}{delta}</strong></div>)}</div></div></AsyncBoundary>}
        </Panel>
      </section>
      <Panel title="导出中心"><div className="export-grid"><ExportCard title="客户版报告" description="当前分析口径的汇总指标与建议。" href={apiPath(`/api/export/summary.xls?project_id=${projectId}`)} label="导出汇总" /><ExportCard title="当前有效结果" description="仅导出每个逻辑任务的当前结果。" href={batchId ? apiPath(`/api/export/batches/${batchId}/runs.xls`) : apiPath(`/api/export/runs.xls?project_id=${projectId}`)} label="导出当前结果" /><ExportCard title="完整尝试历史" description="包含被重试替换的旧结果，供内部审计。" href={batchId ? apiPath(`/api/export/batches/${batchId}/runs.xls?history=1`) : apiPath(`/api/export/runs.xls?project_id=${projectId}&history=1`)} label="导出完整历史" /><ExportCard title="批次汇总" description={batchId ? "当前选中批次的汇总表。" : "请先从顶部选择一个批次。"} href={batchId ? apiPath(`/api/export/batches/${batchId}/summary.xls`) : ""} label="导出批次汇总" /></div></Panel>
    </main>
  );
}

function ExportCard({ title, description, href, label }: { title: string; description: string; href: string; label: string }) {
  return <article className="export-card"><FileDown size={20} /><div><strong>{title}</strong><p>{description}</p></div>{href ? <a className="button ghost" href={href} target="_blank" rel="noreferrer">{label}</a> : <button className="ghost" disabled>{label}</button>}</article>;
}

function comparisonMetricLabel(value: string) { return ({ total_runs: "总样本", included_runs: "纳入样本", excluded_runs: "排除样本", needs_review_runs: "待复核样本", success_runs: "成功样本", failed_runs: "失败样本", success_rate: "成功率", brand_mention_rate: "品牌命中率", citation_rate: "引用率", average_latency_ms: "平均耗时", total_cost_estimate: "预估成本", mention_rate: "品牌命中率", top3_rate: "Top3 概率", owned_citation_rate: "官网引用率", valid_rate: "有效样本率", failure_rate: "失败率", average_rank: "平均排名" } as Record<string, string>)[value] || value; }

function AnalysisWorkspace({ summary, chartRows }: { summary: AnalyticsSummary; chartRows: Array<Record<string, string | number>> }) {
  const quality = summary.sample_quality;
  const visibility = summary.visibility;
  return (
    <>
      <section className="analysis-hero">
        <div>
          <span>{summary.meta.scope === "batch" ? "批次分析" : "项目整体"}</span>
          <h2>{summary.meta.client_name} / {summary.meta.brand_name}</h2>
          <p>样本 {quality.completed} / {quality.planned}，有效 {quality.valid}，失败 {quality.failed}，待完成 {quality.pending}</p>
          <p className="muted">数据截止 {formatDateTime(summary.meta.data_cutoff)} · 生成于 {formatDateTime(summary.meta.generated_at)}{summary.meta.report_version ? ` · 报告 V${summary.meta.report_version.version_no}（${summary.meta.report_version.status}）` : " · 尚无报告版本"}</p>
        </div>
        <div className="quality-strip">
          <RateBar label="有效样本率" value={quality.valid_rate} />
          <RateBar label="失败率" value={quality.failure_rate} danger />
        </div>
      </section>
      <section className="metrics-grid">
        <Metric label="品牌命中率" value={`${visibility.mention_rate}%`} hint={`${visibility.mentioned} / ${visibility.valid_samples} 个有效样本`} />
        <Metric label="Top3 概率" value={`${visibility.top3_rate}%`} hint={`Top1 ${visibility.top1_rate}% / Top5 ${visibility.top5_rate}%`} />
        <Metric label="平均排名" value={visibility.average_rank ?? "-"} hint={`平均提及 ${visibility.avg_mentions_per_sample} 次 / 样本`} />
        <Metric label="官网引用率" value={`${summary.source_analysis.owned_citation_rate}%`} hint={`第三方引用 ${summary.source_analysis.third_party_citation_rate}%`} />
      </section>
      <section className="two-column analysis-main-grid">
        <Panel title="测试平台表现矩阵">
          <div className="chart-box">{chartRows.length ? <Suspense fallback={<AsyncBoundary loading loadingLabel="正在加载图表…" />}><AnalyticsChart rows={chartRows} /></Suspense> : <EmptyState title="暂无模型数据" />}</div>
          <ProviderTable rows={summary.provider_breakdown} />
        </Panel>
        <Panel title="报告建议">
          <div className="recommendation-list">{summary.recommendations.map((item, index) => <div key={item}><strong>{index + 1}</strong><span>{item}</span></div>)}</div>
        </Panel>
      </section>
      <section className="two-column">
        <Panel title="问题类型短板"><QuestionTypeTable rows={summary.question_type_breakdown} /></Panel>
        <Panel title="竞品压制风险"><CompetitorRiskTable rows={summary.competitor_risks} /></Panel>
      </section>
      <section className="two-column">
        <Panel title="引用来源结构"><SourcePanel summary={summary} /></Panel>
        <Panel title="证据样本"><EvidencePanel summary={summary} /></Panel>
      </section>
    </>
  );
}

function RateBar({ label, value, danger = false }: { label: string; value: number; danger?: boolean }) {
  return <div className="rate-bar"><div><span>{label}</span><strong>{value}%</strong></div><em><i className={danger ? "is-danger" : ""} style={{ width: `${Math.min(Math.max(value, 0), 100)}%` }} /></em></div>;
}

function ProviderTable({ rows }: { rows: AnalyticsSummary["provider_breakdown"] }) {
  if (!rows.length) return <EmptyState title="暂无模型表现" />;
  return <div className="data-table dense"><table><thead><tr><th>测试平台 / 模式</th><th>有效 / 失败</th><th>命中率</th><th>Top3</th><th>官网引用</th><th>平均排名</th><th>平均耗时</th></tr></thead><tbody>{rows.map((row) => <tr key={row.name}><td className="wide-cell">{row.name}</td><td>{row.valid} / {row.failed}</td><td>{row.mention_rate}%</td><td>{row.top3_rate}%</td><td>{row.owned_citation_rate}%</td><td>{row.average_rank ?? "-"}</td><td>{Math.round(row.avg_latency_ms)} ms</td></tr>)}</tbody></table></div>;
}

function QuestionTypeTable({ rows }: { rows: AnalyticsSummary["question_type_breakdown"] }) {
  if (!rows.length) return <EmptyState title="暂无问题类型数据" />;
  return <div className="data-table dense"><table><thead><tr><th>问题类型</th><th>样本</th><th>命中率</th><th>Top3</th><th>竞品共现</th><th>高风险</th></tr></thead><tbody>{rows.map((row) => <tr key={row.name}><td>{row.name}</td><td>{row.valid} / {row.total}</td><td>{row.mention_rate}%</td><td>{row.top3_rate}%</td><td>{row.competitor_hit_rate}%</td><td>{row.high_risk}</td></tr>)}</tbody></table></div>;
}

function CompetitorRiskTable({ rows }: { rows: AnalyticsSummary["competitor_risks"] }) {
  if (!rows.length) return <EmptyState title="暂无竞品共现" />;
  return <div className="data-table dense" tabIndex={0} aria-label="竞品压制风险表，可横向滚动"><table><thead><tr><th>竞品</th><th>出现次数</th><th>出现占比</th><th>目标缺席次数</th><th>压制率</th></tr></thead><tbody>{rows.map((row) => <tr key={row.name}><td>{row.name}</td><td>{row.count}</td><td>{row.share_rate}%</td><td>{row.target_absent_count}</td><td>{row.pressure_rate}%</td></tr>)}</tbody></table></div>;
}

function SourcePanel({ summary }: { summary: AnalyticsSummary }) {
  const domains = summary.source_analysis.top_domains;
  return <div className="source-panel"><div className="source-metrics"><Metric label="官网引用率" value={`${summary.source_analysis.owned_citation_rate}%`} /><Metric label="第三方引用率" value={`${summary.source_analysis.third_party_citation_rate}%`} /></div>{domains.length ? <div className="data-table dense"><table><thead><tr><th>域名</th><th>引用次数</th></tr></thead><tbody>{domains.map((item) => <tr key={item.domain}><td>{item.domain}</td><td>{item.count}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无引用域名" />}</div>;
}

function EvidencePanel({ summary }: { summary: AnalyticsSummary }) {
  const failed = summary.evidence.failed;
  const missed = summary.evidence.brand_missed;
  const highRisk = summary.evidence.high_risk;
  return <div className="evidence-list">{failed.length ? <EvidenceGroup title="失败样本" rows={failed} /> : null}{highRisk.length ? <EvidenceGroup title="高风险样本" rows={highRisk} /> : null}{missed.length ? <EvidenceGroup title="品牌缺席样本" rows={missed} /> : null}{!failed.length && !highRisk.length && !missed.length ? <EmptyState title="暂无异常证据样本" /> : null}</div>;
}

function EvidenceGroup({ title, rows }: { title: string; rows: ModelRun[] }) {
  return <div className="evidence-group"><h3>{title}</h3>{rows.slice(0, 4).map((row) => <article key={`${title}-${row.id}`}><strong>{runPlatform(row)}</strong><p>{row.error_message || row.response_text || row.question || "-"}</p>{row.batch_id ? <Link to={`/batches/${row.batch_id}?project_id=${row.project_id || ""}&run_id=${row.run_id || row.id}`}>查看具体任务证据</Link> : null}</article>)}</div>;
}

export function SettingsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: systemApi.health });
  const readiness = useQuery({ queryKey: ["health", "ready"], queryFn: systemApi.ready, retry: false });
  const sources = useQuery({ queryKey: ["sources", "health"], queryFn: systemApi.sources });
  const probe = useMutation({
    mutationFn: systemApi.probeSource,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources", "health"] })
  });
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
          <AsyncBoundary loading={readiness.isLoading} error={readiness.error}>
            <div className="readiness-grid">{Object.entries(readiness.data?.checks || {}).map(([name, check]) => <div key={name}><span className={check.ok ? "health-dot is-ok" : "health-dot is-error"} /> <strong>{name}</strong><em>{check.ok ? check.skipped ? check.reason || "已跳过" : "就绪" : check.error || "异常"}</em></div>)}</div>
          </AsyncBoundary>
        </Panel>
        <Panel title="信息源就绪度">
          <AsyncBoundary loading={sources.isLoading} error={sources.error} empty={!sources.data?.sources.length} emptyLabel="暂无信息源配置" onRetry={() => sources.refetch()}>
            <div className="source-health-list">{sources.data?.sources.map((source) => <article key={source.model_config_id}><div><strong>{source.label}</strong><span>{source.source}</span></div><div className="health-modes"><em className={source.modes.pure.ready ? "is-ready" : "is-blocked"}>本体 {source.modes.pure.ready ? "就绪" : "不可用"}</em><em className={source.modes.search.ready ? "is-ready" : "is-blocked"}>联网 {source.modes.search.ready ? "就绪" : "不可用"}</em></div><button className="ghost" disabled={probe.isPending} onClick={() => probe.mutate(source.source)}>静态探针</button></article>)}</div>
          </AsyncBoundary>
          {probe.error ? <div className="error-box">{probe.error.message}</div> : null}
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

function BatchTable({ batches }: { batches: SamplingBatch[] }) {
  if (!batches.length) return <EmptyState title="暂无批次" />;
  return <div className="data-table"><table><thead><tr><th>批次</th><th>状态</th><th>进度</th><th>成功 / 失败</th><th>创建时间</th></tr></thead><tbody>{batches.map((batch) => { const c = asCount(batch); return <tr key={batch.batch_id}><td><Link className="batch-link" to={`/batches/${batch.batch_id}?project_id=${batch.project_id}`}><strong>{batch.batch_name || batch.batch_id}</strong>{batch.batch_name ? <span>{batch.batch_id}</span> : null}</Link></td><td><StatusBadge status={batch.status} /></td><td>{c.completed} / {c.total}</td><td>{c.success} / {c.failed}</td><td>{formatDateTime(batch.created_at)}</td></tr>; })}</tbody></table></div>;
}

function RunsTable({ runs, onReview, focusedRunId = "" }: { runs: ModelRun[]; onReview?: (run: ModelRun) => void; focusedRunId?: string }) {
  const [expandedRunIds, setExpandedRunIds] = useState<Set<string>>(() => new Set());
  if (!runs.length) return <EmptyState title="暂无运行明细" />;
  return (
    <div className="data-table dense run-detail-table">
      <table>
        <thead>
          <tr>
            <th>问题ID</th>
            <th>问题内容</th>
            <th>问题类型</th>
            <th>产品线</th>
            <th>平台</th>
            <th>回答原文</th>
            <th>引用来源</th>
            <th>测试时间</th>
            <th>运行ID（内部信息）</th>
            <th>批次ID（内部信息）</th>
            <th>测试平台（内部信息）</th>
            <th>联网搜索（内部信息）</th>
            <th>状态（内部信息）</th>
            <th>耗时（内部信息）</th>
            <th>错误信息（内部信息）</th>
            <th>详情</th>
            {onReview ? <th>质检</th> : null}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const runKey = String(run.run_id || run.id);
            const detailId = `run-detail-${runKey}`;
            const expanded = expandedRunIds.has(runKey);
            const citations = citationUrlList(run.citations_json);
            const toggleExpanded = () => setExpandedRunIds((current) => {
              const next = new Set(current);
              if (next.has(runKey)) next.delete(runKey);
              else next.add(runKey);
              return next;
            });
            return (
              <Fragment key={run.id}>
                <tr id={`run-${runKey}`} className={`run-summary-row ${focusedRunId === runKey ? "is-focused" : ""} ${expanded ? "is-expanded" : ""}`.trim()}>
                  <td><div className="run-cell-clamp">{run.source_question_id || "-"}</div></td>
                  <td className="question-content-cell"><div className="run-cell-clamp">{run.question || "-"}</div></td>
                  <td><div className="run-cell-clamp">{run.question_type || "-"}</div></td>
                  <td><div className="run-cell-clamp">{run.product_line || "-"}</div></td>
                  <td><div className="run-cell-clamp">{runPlatform(run)}</div></td>
                  <td className="answer-text-cell"><div className="run-cell-clamp">{run.response_text || "-"}</div></td>
                  <td className="citation-source-cell"><div className="run-cell-clamp">{citations.join("; ") || "-"}</div></td>
                  <td><div className="run-cell-clamp">{formatDateTime(run.requested_at)}</div></td>
                  <td><div className="run-cell-clamp">{run.run_id || "-"}</div></td>
                  <td><div className="run-cell-clamp">{run.batch_id || "-"}</div></td>
                  <td><div className="run-cell-clamp">{runPlatform(run)}</div></td>
                  <td><div className="run-cell-clamp">{run.search_enabled ? "是" : "否"}</div></td>
                  <td><div className="run-cell-clamp">{run.status || "-"}</div></td>
                  <td><div className="run-cell-clamp">{run.latency_ms || 0} ms</div></td>
                  <td className="error-message-cell"><div className="run-cell-clamp">{run.error_message || "-"}</div></td>
                  <td><button className="ghost run-detail-toggle" type="button" aria-expanded={expanded} aria-controls={detailId} onClick={toggleExpanded}>{expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}{expanded ? "收起" : "展开"}</button></td>
                  {onReview ? <td><button className="ghost" type="button" onClick={() => onReview(run)}><ClipboardCheck size={14} />标记</button></td> : null}
                </tr>
                {expanded ? (
                  <tr className="run-expanded-row">
                    <td colSpan={onReview ? 17 : 16}>
                      <section id={detailId} className="run-expanded-detail" aria-label={`${run.source_question_id || runKey} 运行详情`}>
                        <div className="run-detail-copy">
                          <article><h3>问题内容</h3><p>{run.question || "-"}</p></article>
                          <article><h3>回答原文</h3><p>{run.response_text || "-"}</p></article>
                        </div>
                        <aside>
                          <article>
                            <h3>引用来源</h3>
                            {citations.length ? <ol>{citations.map((url) => <li key={url}><a href={url} target="_blank" rel="noreferrer">{url}<ExternalLink size={12} /></a></li>)}</ol> : <p>暂无引用</p>}
                          </article>
                          <dl>
                            <dt>运行 ID</dt><dd>{run.run_id || "-"}</dd>
                            <dt>批次 ID</dt><dd>{run.batch_id || "-"}</dd>
                            <dt>平台</dt><dd>{runPlatform(run)}</dd>
                            <dt>状态</dt><dd>{run.status || "-"}</dd>
                            <dt>耗时</dt><dd>{run.latency_ms || 0} ms</dd>
                            <dt>测试时间</dt><dd>{formatDateTime(run.requested_at)}</dd>
                          </dl>
                          {run.error_message ? <article className="run-detail-error"><h3>错误信息</h3><p>{run.error_message}</p></article> : null}
                        </aside>
                      </section>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProgressBar({ batch }: { batch?: { total_count?: number; completed_count?: number; total?: number; completed?: number } }) {
  const c = asCount(batch || {});
  return <div className="progress"><div style={{ width: `${pct(c.completed, c.total)}%` }} /><span>{c.completed} / {c.total}</span></div>;
}

function SourceStatusList({ rows, compact = false }: { rows: SourceRunStatus[]; compact?: boolean }) {
  if (!rows.length) return <EmptyState title="等待平台状态" />;
  return (
    <div className={`source-status-list ${compact ? "is-compact" : ""}`}>
      {rows.map((row) => (
        <article key={`${row.test_platform}-${row.model}`} className={`source-status-card status-line-${row.status}`}>
          <header>
            <strong>{row.test_platform}</strong>
            <StatusBadge status={row.status} />
          </header>
          <div className="source-status-grid">
            <span>进度 <b>{row.completed} / {row.total}</b></span>
            <span>成功 <b>{row.success}</b></span>
            <span>失败 <b>{row.failed}</b></span>
            <span>排队 <b>{row.queued}</b></span>
            <span>运行 <b>{row.running}</b></span>
            <span>均耗时 <b>{row.avg_latency_ms || 0} ms</b></span>
          </div>
          {row.last_error ? <p>{row.last_error}</p> : null}
        </article>
      ))}
    </div>
  );
}

function ModelHealth({ model }: { model: ModelConfig }) {
  return <div className="model-health"><strong>{model.label}</strong><span>{model.provider}</span><em>{model.has_key ? "Key 已配置" : "缺少 Key"}</em></div>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="panel"><h2>{title}</h2>{children}</section>;
}
