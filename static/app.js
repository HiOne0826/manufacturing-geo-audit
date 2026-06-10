const APP_BASE_PATH = (() => {
  const path = window.location.pathname || "/";
  if (path === "/" || !path.includes("/")) return "";
  return path.endsWith("/") ? path.slice(0, -1) : path;
})();

function withBasePath(path) {
  if (!path.startsWith("/")) return path;
  return APP_BASE_PATH ? `${APP_BASE_PATH}${path}` : path;
}

const state = {
  projects: [],
  questions: [],
  models: [],
  presets: {},
  samplingDrafts: {},
  samplingJob: null,
  selectedProjectId: null,
  activePage: "projects",
};

const questionAutosaveTimers = new Map();

const PROVIDER_SAMPLING_DEFAULTS = {
  openai: {
    temperature: "1",
    reasoning_effort: "medium",
    defaults_note: "temperature 默认 1；reasoning.effort 默认 medium。",
  },
  gemini: {
    thinking_budget: "0",
    defaults_note: "thinkingBudget 设为 0 表示关闭思考；留空时走模型动态默认。",
  },
  qwen: {
    reasoning_effort: "",
    search_strategy: "turbo",
    defaults_note: "联网搜索默认走 enable_search；search_strategy 预填 turbo 便于采样。",
  },
  kimi: {
    temperature: "1",
    defaults_note: "Kimi K2.5 联网搜索与深度思考不同时开启；当前运行温度固定按 1 处理。",
  },
};

const QUESTION_TEMPLATE_ROWS = [
  {
    问题内容: "汽车白车身多材料连接，国内有哪些FDS热熔螺接设备品牌值得推荐？",
    问题类型: "品牌推荐",
    产品线: "FDS",
    采购阶段: "认知阶段",
    场景: "汽车焊装/轻量化连接",
    优先级: "高",
    建议测试平台: "ChatGPT;DeepSeek;豆包;元宝;千问;Gemini",
    备注: "首轮核心样本，可优先筛选高优先级问题",
    拜访前30题: "是",
    首轮顺序: 1,
    筛选理由: "FDS品牌推荐首题，直接测AI是否知道董泰尔",
  },
];

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const res = await fetch(withBasePath(path), { ...options, headers, credentials: "same-origin" });
  const data = await res.json();
  if (res.status === 401) {
    showAuthGate(data.error || "请先登录");
  }
  if (!res.ok || data.error) throw new Error(data.error || "请求失败");
  return data;
}

function showAuthGate(message = "") {
  document.body.classList.add("auth-required");
  document.getElementById("authGate")?.classList.remove("hidden");
  const authMessage = document.getElementById("authMessage");
  if (authMessage) authMessage.textContent = message;
  window.setTimeout(() => document.getElementById("authPasswordInput")?.focus(), 0);
}

function hideAuthGate() {
  document.body.classList.remove("auth-required");
  document.getElementById("authGate")?.classList.add("hidden");
  const authMessage = document.getElementById("authMessage");
  if (authMessage) authMessage.textContent = "";
}

async function authRequest(path, payload = null) {
  const options = {
    method: payload ? "POST" : "GET",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
  };
  if (payload) options.body = JSON.stringify(payload);
  const res = await fetch(withBasePath(path), options);
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || "请求失败");
  return data;
}

async function loadAuthStatus() {
  const status = await authRequest("/api/auth/status");
  document.getElementById("logoutBtn")?.classList.toggle("hidden", !status.auth_enabled);
  return status;
}

function currentProjectId() {
  return Number(state.selectedProjectId || 0);
}

function projectOptions(selectedValue, includeAll = false) {
  const rows = [];
  if (includeAll) rows.push(`<option value="all"${selectedValue === "all" ? " selected" : ""}>全部项目</option>`);
  for (const project of state.projects) {
    const selected = String(selectedValue) === String(project.id) ? " selected" : "";
    rows.push(`<option value="${project.id}"${selected}>${project.client_name} / ${project.brand_name}</option>`);
  }
  return rows.join("");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function parseCitations(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function renderCitationList(row) {
  const citations = parseCitations(row.citations_json);
  if (!citations.length) return '<span class="muted">无</span>';
  return citations
    .slice(0, 3)
    .map((item) => {
      const url = String(item.url || "").trim();
      const title = String(item.title || url || "未命名来源").trim();
      if (!url) return `<div>${escapeHtml(title)}</div>`;
      return `<div><a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a></div>`;
    })
    .join("");
}

function getSamplingDraft(modelId) {
  if (!state.samplingDrafts[modelId]) {
    const row = state.models.find((item) => Number(item.id) === Number(modelId)) || {};
    const defaults = PROVIDER_SAMPLING_DEFAULTS[row.provider] || {};
    state.samplingDrafts[modelId] = {
      runtime_model: "",
      runtime_model_version: "",
      expanded: false,
      temperature: defaults.temperature || "",
      reasoning_effort: defaults.reasoning_effort || "",
      thinking_budget: defaults.thinking_budget || "",
      search_sources: "",
      search_limit: "",
      search_max_keyword: "",
      search_user_location: "",
      search_site_filter: "",
      search_time_filter: "",
      search_strategy: defaults.search_strategy || "",
      search_freshness: "",
      search_prompt_intervene: "",
      search_enable_source: false,
      search_enable_citation: false,
      search_citation_format: "",
    };
  }
  return state.samplingDrafts[modelId];
}

function getProviderSamplingDefaults(row) {
  return PROVIDER_SAMPLING_DEFAULTS[row.provider] || {};
}

function readingOrFallback(value, fallback = "模型默认") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function getReasoningChoices(row) {
  if (String(row.reasoning_levels || "").includes("budget:")) {
    return '<option value="">模型默认</option>';
  }
  const levels = String(row.reasoning_levels || "")
    .split(/[;,\n/|]+/)
    .map((item) => item.trim())
    .filter((item) => item && !item.startsWith("budget:") && !["按模型能力", "disabled", "enabled", "disabled + low", "enabled + low"].includes(item));
  const unique = [...new Set(levels)];
  const base = ['<option value="">模型默认</option>'];
  for (const level of unique) {
    base.push(`<option value="${escapeHtml(level)}">${escapeHtml(level)}</option>`);
  }
  if (unique.length < 2) {
    for (const level of ["none", "minimal", "low", "medium", "high", "xhigh"]) {
      if (!unique.includes(level)) base.push(`<option value="${level}">${level}</option>`);
    }
  }
  return base.join("");
}

function parseModelVersionOptions(row) {
  const values = new Set();
  if (row.model) values.add(String(row.model).trim());
  for (const chunk of String(row.model_version || "").split(/[\n,;|]+/)) {
    const value = chunk.trim();
    if (value) values.add(value);
  }
  return [...values];
}

function samplingProviderNote(row) {
  if (row.provider === "kimi") {
    return "Kimi 联网搜索走官方 builtin_function.$web_search；开启联网搜索时必须关闭深度思考。当前 kimi-k2.5 运行时会固定使用 temperature=1。";
  }
  if (row.provider === "doubao") {
    return "豆包联网搜索走 Responses API；高级搜索参数仅在服务商支持时生效。";
  }
  if (row.provider === "qwen") {
    return "通义千问联网搜索按阿里云百炼文档走 enable_search=true；高级项通过 search_options 下发，只作用于本次采样。";
  }
  return "模型库负责保存基线配置，这里的设置只作用于本次采样。";
}

function renderTable(el, columns, rows) {
  if (!rows.length) {
    el.innerHTML = `<thead><tr>${columns.map((c) => `<th>${c.label}</th>`).join("")}</tr></thead><tbody><tr><td colspan="${columns.length}">暂无数据</td></tr></tbody>`;
    return;
  }
  el.innerHTML = `
    <thead><tr>${columns.map((c) => `<th>${c.label}</th>`).join("")}</tr></thead>
    <tbody>
      ${rows
        .map(
          (row) =>
            `<tr>${columns
              .map((c) => `<td>${c.render ? c.render(row) : escapeHtml(row[c.key])}</td>`)
              .join("")}</tr>`
        )
        .join("")}
    </tbody>
  `;
}

function setExportLinks() {
  const id = currentProjectId();
  const exportRunsFromHistory = document.getElementById("exportRunsFromHistory");
  const exportSummary = document.getElementById("exportSummary");
  if (exportRunsFromHistory) {
    exportRunsFromHistory.dataset.projectId = id || "";
    exportRunsFromHistory.href = id ? withBasePath(`/api/export/runs.xls?project_id=${id}`) : "#";
    exportRunsFromHistory.setAttribute("download", "geo-runs.xls");
    exportRunsFromHistory.setAttribute("target", "_blank");
  }
  if (exportSummary) {
    exportSummary.dataset.projectId = id || "";
    exportSummary.href = id ? withBasePath(`/api/export/summary.xls?project_id=${id}`) : "#";
    exportSummary.setAttribute("download", "geo-summary.xls");
    exportSummary.setAttribute("target", "_blank");
  }
}

function updateSamplingHistoryHint(projectId, runCount = null) {
  const hint = document.getElementById("samplingHistoryHint");
  if (!hint) return;
  const project = state.projects.find((item) => Number(item.id) === Number(projectId));
  if (!projectId || !project) {
    hint.textContent = "未选择项目";
    return;
  }
  const projectName = `${project.client_name} / ${project.brand_name}`;
  if (runCount === null) {
    hint.textContent = `当前项目：${projectName}`;
    return;
  }
  hint.textContent = `当前项目：${projectName} · 历史结果 ${runCount} 条`;
}

function setExportFeedback(message, isError = false) {
  const el = document.getElementById("exportFeedback");
  if (!el) return;
  if (!message) {
    el.classList.add("hidden");
    el.textContent = "";
    el.style.borderColor = "";
    el.style.background = "";
    return;
  }
  el.classList.remove("hidden");
  el.textContent = message;
  el.style.borderColor = isError ? "#f1c4bf" : "#dbe7ff";
  el.style.background = isError ? "#fff5f4" : "#f8fbff";
}

function setSamplingProgress({ label, percent, detail }) {
  const progressLabel = document.getElementById("samplingProgressLabel");
  const progressText = document.getElementById("samplingProgressText");
  const progressBar = document.getElementById("samplingProgressBar");
  if (progressLabel) progressLabel.textContent = label || "等待采样";
  if (progressText) progressText.textContent = detail || `${Math.round(percent || 0)}%`;
  if (progressBar) progressBar.style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
}

function stopSamplingJobPolling() {
  if (state.samplingJob?.timerId) {
    clearInterval(state.samplingJob.timerId);
  }
  state.samplingJob = null;
  const startBtn = document.getElementById("startRunBtn");
  if (startBtn) startBtn.disabled = false;
}

async function pollSamplingJob(batchId, projectId) {
  const progress = await api(`/api/runs/progress?batch_id=${encodeURIComponent(batchId)}`);
  const total = Number(progress.total || 0);
  const completed = Number(progress.completed || 0);
  const percent = total > 0 ? (completed / total) * 100 : 0;
  const detail = total > 0 ? `${completed} / ${total}` : "0%";
  const currentModel = progress.current_model ? ` · ${progress.current_model}` : "";
  if (progress.status === "queued") {
    document.getElementById("runStatus").textContent = "排队中";
    setSamplingProgress({ label: "采样任务已创建", percent: 0, detail });
    return;
  }
  if (progress.status === "running") {
    document.getElementById("runStatus").textContent = "采样中";
    setSamplingProgress({ label: `正在采样${currentModel}`, percent, detail });
    return;
  }
  if (progress.status === "completed") {
    document.getElementById("runStatus").textContent = `完成：${progress.success}/${progress.total} 成功`;
    setSamplingProgress({ label: "采样完成", percent: 100, detail: `${progress.total} / ${progress.total}` });
    stopSamplingJobPolling();
    await loadRuns();
    await loadAnalytics();
    document.getElementById("runsTable")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (progress.status === "failed") {
    document.getElementById("runStatus").textContent = "采样失败";
    setSamplingProgress({ label: "采样失败", percent, detail });
    stopSamplingJobPolling();
    throw new Error(progress.error || "采样任务执行失败");
  }
}

function startSamplingJobPolling(batchId, projectId) {
  stopSamplingJobPolling();
  const startBtn = document.getElementById("startRunBtn");
  if (startBtn) startBtn.disabled = true;
  state.samplingJob = {
    batchId,
    projectId,
    timerId: window.setInterval(async () => {
      try {
        await pollSamplingJob(batchId, projectId);
      } catch (error) {
        console.error("pollSamplingJob failed", error);
        stopSamplingJobPolling();
        document.getElementById("runStatus").textContent = "采样失败";
        alert(error.message);
      }
    }, 1200),
  };
}

function syncProjectSelectors() {
  const selected = String(currentProjectId() || "");
  const simpleSelectors = [
    document.getElementById("activeProjectSelect"),
    document.getElementById("questionImportProjectSelect"),
    document.getElementById("samplingProjectSelect"),
    document.getElementById("analysisProjectSelect"),
  ];
  for (const select of simpleSelectors) {
    if (select) select.innerHTML = projectOptions(selected);
  }
  const filter = document.getElementById("questionFilterProjectSelect");
  if (filter) filter.innerHTML = projectOptions(selected || "all", true);
}

function setPage(page) {
  state.activePage = page;
  document.querySelectorAll(".page").forEach((el) => el.classList.toggle("active", el.id === `page-${page}`));
  document.querySelectorAll("[data-page-link]").forEach((el) => {
    el.classList.toggle("active", el.dataset.pageLink === page);
  });
}

function initRouting() {
  const hash = window.location.hash.replace("#", "");
  const page = ["projects", "questions", "models", "sampling", "analysis"].includes(hash) ? hash : "projects";
  setPage(page);
}

function parseForm(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const box of form.querySelectorAll('input[type="checkbox"]')) {
    data[box.name] = box.checked;
  }
  return data;
}

function parseSemicolonList(value) {
  return String(value || "")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean);
}

function stringifySemicolonList(items) {
  return items.map((item) => item.trim()).filter(Boolean).join(";");
}

function renderCompetitorOptions(value, id) {
  const chips = parseSemicolonList(value)
    .map(
      (item, index) => `
        <span class="tag-chip">
          ${escapeHtml(item)}
          <button type="button" class="tag-chip-remove project-competitor-remove" data-id="${id}" data-index="${index}">×</button>
        </span>
      `
    )
    .join("");
  return `
    <div class="project-competitor-options" data-id="${id}">
      <div class="tag-input-chips">${chips}</div>
      <input data-competitor-option-input="${id}" class="tag-input-field" type="text" placeholder="输入竞品后回车" />
      <input data-field="competitors" data-id="${id}" type="hidden" value="${escapeHtml(value || "")}" />
    </div>
  `;
}

function initTagInput(rootId, hiddenId) {
  const root = document.getElementById(rootId);
  const hidden = document.getElementById(hiddenId);
  const chipsRoot = root.querySelector(".tag-input-chips");
  const field = root.querySelector(".tag-input-field");
  const render = () => {
    const items = parseSemicolonList(hidden.value);
    chipsRoot.innerHTML = items
      .map((item, index) => `<span class="tag-chip">${escapeHtml(item)}<button type="button" class="tag-chip-remove" data-index="${index}">×</button></span>`)
      .join("");
  };
  const appendValue = (raw) => {
    const value = raw.trim();
    if (!value) return;
    const items = parseSemicolonList(hidden.value);
    if (!items.includes(value)) items.push(value);
    hidden.value = stringifySemicolonList(items);
    field.value = "";
    render();
  };
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === "," || event.key === "，") {
      event.preventDefault();
      appendValue(field.value);
    }
  });
  field.addEventListener("blur", () => appendValue(field.value));
  chipsRoot.addEventListener("click", (event) => {
    const target = event.target;
    if (!target.classList.contains("tag-chip-remove")) return;
    const items = parseSemicolonList(hidden.value);
    items.splice(Number(target.dataset.index), 1);
    hidden.value = stringifySemicolonList(items);
    render();
  });
  render();
  return {
    sync(value) {
      hidden.value = value || "";
      render();
    },
  };
}

async function loadProjects() {
  const data = await api("/api/projects");
  state.projects = data.projects;
  if (!state.selectedProjectId && data.projects[0]) state.selectedProjectId = data.projects[0].id;
  if (state.selectedProjectId && !data.projects.find((item) => item.id === Number(state.selectedProjectId))) {
    state.selectedProjectId = data.projects[0]?.id || null;
  }
  syncProjectSelectors();
  setExportLinks();
  renderProjects();
}

function renderProjects() {
  renderTable(
    document.getElementById("projectsTable"),
    [
      { key: "id", label: "选择", render: (row) => `<button class="ghost-btn select-project-btn" data-id="${row.id}">${Number(row.id) === Number(currentProjectId()) ? "当前项目" : "设为当前"}</button>` },
      { key: "client_name", label: "客户名称", render: (row) => `<input data-field="client_name" data-id="${row.id}" value="${escapeHtml(row.client_name)}" />` },
      { key: "brand_name", label: "品牌名称", render: (row) => `<input data-field="brand_name" data-id="${row.id}" value="${escapeHtml(row.brand_name)}" />` },
      { key: "product_category", label: "产品品类", render: (row) => `<input data-field="product_category" data-id="${row.id}" value="${escapeHtml(row.product_category)}" />` },
      { key: "target_region", label: "目标地区", render: (row) => `<input data-field="target_region" data-id="${row.id}" value="${escapeHtml(row.target_region)}" />` },
      { key: "website_domain", label: "官网域名", render: (row) => `<input data-field="website_domain" data-id="${row.id}" value="${escapeHtml(row.website_domain)}" />` },
      { key: "competitors", label: "竞品列表", render: (row) => renderCompetitorOptions(row.competitors, row.id) },
      { key: "question_count", label: "问题数" },
      { key: "run_count", label: "运行数" },
      {
        key: "actions",
        label: "操作",
        render: (row) => `
          <div class="table-actions">
            <button class="save-project-btn" data-id="${row.id}">保存</button>
            <button class="ghost-btn delete-project-btn" data-id="${row.id}">删除</button>
          </div>
        `,
      },
    ],
    state.projects
  );
}

async function loadModels() {
  const data = await api("/api/models");
  state.models = data.models;
  state.presets = data.presets || {};
  renderModelPresets();
  renderModels();
  renderSamplingModels();
}

function renderModelPresets() {
  const root = document.getElementById("modelPresetButtons");
  root.innerHTML = Object.values(state.presets)
    .map(
      (preset) =>
        `<button type="button" class="ghost-btn model-preset-btn" data-provider="${escapeHtml(preset.provider)}">${escapeHtml(preset.label)}</button>`
    )
    .join("");
}

function modeTags(row) {
  const tags = [];
  if (row.supports_pure) tags.push('<span class="tag">纯模型</span>');
  if (row.supports_search) tags.push('<span class="tag">联网搜索</span>');
  return tags.join(" ");
}

function capabilityTags(row) {
  const tags = [];
  if (row.supports_reasoning) tags.push('<span class="tag">深度思考</span>');
  if (row.supports_citation) tags.push('<span class="tag">引用</span>');
  if (row.supports_site_filter) tags.push('<span class="tag">站点筛选</span>');
  if (row.supports_time_filter) tags.push('<span class="tag">时间筛选</span>');
  if (row.supports_user_location) tags.push('<span class="tag">地区定位</span>');
  if (row.supports_tool_calling) tags.push('<span class="tag">工具调用</span>');
  return tags.join(" ");
}

function primaryCapabilityText(row) {
  const items = [];
  if (row.supports_pure) items.push("纯模型");
  if (row.supports_search) items.push("联网搜索");
  if (row.supports_reasoning) items.push("深度思考");
  if (row.supports_citation) items.push("引用");
  return items.join(" / ") || "未配置能力";
}

function renderModels() {
  const root = document.getElementById("modelsTable");
  root.innerHTML = state.models
    .map(
      (row) => `
        <article class="model-list-row" data-model-card="${row.id}">
          <div class="model-list-main">
            <div>
              <strong>${escapeHtml(row.label)}</strong>
              <span>${escapeHtml(row.provider)} · ${escapeHtml(row.api_family || "未填写 API 家族")}</span>
            </div>
            <code>${escapeHtml(row.model)}</code>
          </div>
          <div class="model-list-meta">
            <span class="tag ${row.has_key ? "" : "warn"}">${row.has_key ? `Key：${escapeHtml(row.api_key_masked || "已配置")}` : "未配置 Key"}</span>
            <span class="tag">${escapeHtml(primaryCapabilityText(row))}</span>
            <span class="tag ${row.active ? "" : "warn"}">${row.active ? "启用" : "停用"}</span>
          </div>
          <div class="model-list-actions">
            <button class="ghost-btn model-detail-btn" data-id="${row.id}">详情设置</button>
            <button class="test-model-btn" data-id="${row.id}">测试</button>
            <button class="ghost-btn delete-model-btn" data-id="${row.id}">删除</button>
          </div>
        </article>
      `
    )
    .join("");
}

function renderSamplingModels() {
  const root = document.getElementById("samplingModelList");
  root.innerHTML = state.models
    .filter((row) => row.active)
    .map((row) => {
      const draft = getSamplingDraft(row.id);
      const defaults = getProviderSamplingDefaults(row);
      const summaryTemperature = readingOrFallback(draft.temperature, defaults.temperature || "模型默认");
      const summaryReasoning = readingOrFallback(draft.reasoning_effort, defaults.reasoning_effort || "模型默认");
      const summaryBudget = readingOrFallback(draft.thinking_budget, defaults.thinking_budget || "0 / 模型默认");
      const officialDefaultMeta = defaults.defaults_note
        ? `<div class="sampling-default-note"><span>官方默认</span><strong>${escapeHtml(defaults.defaults_note)}</strong></div>`
        : "";
      const versionOptions = parseModelVersionOptions(row);
      const versionPicker =
        versionOptions.length > 1
          ? `
            <label class="sampling-subfield">
              <span>本次模型版本</span>
              <select data-sampling-field="runtime_model" data-model-config-id="${row.id}">
                ${versionOptions
                  .map((item) => {
                    const selected = (draft.runtime_model || row.model) === item ? " selected" : "";
                    return `<option value="${escapeHtml(item)}"${selected}>${escapeHtml(item)}</option>`;
                  })
                  .join("")}
              </select>
            </label>
          `
          : `
            <label class="sampling-subfield">
              <span>本次模型 ID</span>
              <input
                data-sampling-field="runtime_model"
                data-model-config-id="${row.id}"
                value="${escapeHtml(draft.runtime_model || row.model || "")}"
                placeholder="${escapeHtml(row.model || "填写本次运行模型 ID")}"
              />
            </label>
          `;
      const search = row.supports_search
        ? `<label class="sampling-mode is-optional"><input type="checkbox" data-model-config-id="${row.id}" data-sampling-option="search" disabled />联网搜索</label>`
        : "";
      const reasoning = row.supports_reasoning
        ? `<label class="sampling-mode is-optional"><input type="checkbox" data-model-config-id="${row.id}" data-sampling-option="reasoning" disabled />深度思考</label>`
        : "";
      const searchDetailBlock = row.supports_search
        ? row.provider === "kimi"
          ? `
              <div class="sampling-runtime-block sampling-search-detail provider-note">
                <div class="sampling-runtime-title">Kimi 联网搜索说明</div>
                <p class="sampling-inline-help">按官方文档，Kimi 通过 <code>builtin_function.$web_search</code> 完成联网搜索。当前不建议也不允许和深度思考同时开启，因此这里不再展示额外搜索参数输入框。</p>
              </div>
            `
          : row.provider === "qwen"
            ? `
              <div class="sampling-runtime-block sampling-search-detail">
                <div class="sampling-runtime-title">通义千问联网搜索配置</div>
                <div class="sampling-runtime-grid">
                  <label class="sampling-subfield">
                    <span>搜索策略</span>
                    <select data-sampling-field="search_strategy" data-model-config-id="${row.id}">
                      <option value=""${!draft.search_strategy ? " selected" : ""}>默认</option>
                      <option value="turbo"${draft.search_strategy === "turbo" ? " selected" : ""}>turbo</option>
                      <option value="standard"${draft.search_strategy === "standard" ? " selected" : ""}>standard</option>
                      <option value="pro"${draft.search_strategy === "pro" ? " selected" : ""}>pro</option>
                    </select>
                  </label>
                  <label class="sampling-subfield">
                    <span>时间新鲜度</span>
                    <input
                      data-sampling-field="search_freshness"
                      data-model-config-id="${row.id}"
                      type="number"
                      min="1"
                      value="${escapeHtml(draft.search_freshness || "")}"
                      placeholder="如 30，表示近 30 天"
                    />
                  </label>
                  <label class="sampling-subfield">
                    <span>站点筛选</span>
                    <input
                      data-sampling-field="search_site_filter"
                      data-model-config-id="${row.id}"
                      value="${escapeHtml(draft.search_site_filter || "")}"
                      placeholder="如 abc.com, xyz.com"
                    />
                  </label>
                  <label class="sampling-subfield">
                    <span>提示词干预</span>
                    <input
                      data-sampling-field="search_prompt_intervene"
                      data-model-config-id="${row.id}"
                      value="${escapeHtml(draft.search_prompt_intervene || "")}"
                      placeholder="可选，补充搜索意图"
                    />
                  </label>
                  <label class="sampling-subfield checkbox-subfield">
                    <span>返回来源信息</span>
                    <input
                      data-sampling-field="search_enable_source"
                      data-model-config-id="${row.id}"
                      type="checkbox"
                      ${draft.search_enable_source ? "checked" : ""}
                    />
                  </label>
                  <label class="sampling-subfield checkbox-subfield">
                    <span>返回引用</span>
                    <input
                      data-sampling-field="search_enable_citation"
                      data-model-config-id="${row.id}"
                      type="checkbox"
                      ${draft.search_enable_citation ? "checked" : ""}
                    />
                  </label>
                  <label class="sampling-subfield">
                    <span>引用格式</span>
                    <input
                      data-sampling-field="search_citation_format"
                      data-model-config-id="${row.id}"
                      value="${escapeHtml(draft.search_citation_format || "")}"
                      placeholder="按官方枚举填写；留空默认"
                    />
                  </label>
                </div>
                <p class="sampling-inline-help">对应阿里云百炼 <code>enable_search=true</code> 和 <code>search_options</code>。如果模型未开通联网能力，接口会直接返回错误。</p>
              </div>
            `
          : `
              <div class="sampling-runtime-block sampling-search-detail">
                <div class="sampling-runtime-title">联网搜索详细配置</div>
                <div class="sampling-runtime-grid">
                  <label class="sampling-subfield">
                    <span>搜索来源</span>
                    <input
                      data-sampling-field="search_sources"
                      data-model-config-id="${row.id}"
                      value="${escapeHtml(draft.search_sources || "")}"
                      placeholder="按服务商文档填写；留空则走默认"
                    />
                  </label>
                  <label class="sampling-subfield">
                    <span>结果条数</span>
                    <input
                      data-sampling-field="search_limit"
                      data-model-config-id="${row.id}"
                      type="number"
                      min="1"
                      value="${escapeHtml(draft.search_limit || "")}"
                      placeholder="如 5"
                    />
                  </label>
                  <label class="sampling-subfield">
                    <span>关键词上限</span>
                    <input
                      data-sampling-field="search_max_keyword"
                      data-model-config-id="${row.id}"
                      value="${escapeHtml(draft.search_max_keyword || "")}"
                      placeholder="如 8"
                    />
                  </label>
                  <label class="sampling-subfield">
                    <span>地区定位</span>
                    <input
                      data-sampling-field="search_user_location"
                      data-model-config-id="${row.id}"
                      value="${escapeHtml(draft.search_user_location || "")}"
                      placeholder="如 上海 / 北京"
                    />
                  </label>
                  <label class="sampling-subfield">
                    <span>站点筛选</span>
                    <input
                      data-sampling-field="search_site_filter"
                      data-model-config-id="${row.id}"
                      value="${escapeHtml(draft.search_site_filter || "")}"
                      placeholder="如 abc.com, xyz.com"
                    />
                  </label>
                  <label class="sampling-subfield">
                    <span>时间筛选</span>
                    <input
                      data-sampling-field="search_time_filter"
                      data-model-config-id="${row.id}"
                      value="${escapeHtml(draft.search_time_filter || "")}"
                      placeholder="如 近30天 / 2026-06"
                    />
                  </label>
                </div>
                <p class="sampling-inline-help">这些字段只作用于本次采样，不会回写模型库；具体生效项取决于服务商接口是否支持。</p>
              </div>
            `
        : "";
      return `
        <details class="sampling-card" data-sampling-card="${row.id}" ${draft.expanded ? "open" : ""}>
          <summary class="sampling-card-summary">
            <div class="sampling-card-title">
              <div class="sampling-head">
                <div>
                  <strong>${escapeHtml(row.label)}</strong>
                  <div>${escapeHtml(row.model)}</div>
                  <div class="sampling-meta">${escapeHtml(row.api_family || "")}</div>
                </div>
                <div>${row.has_key ? '<span class="status-dot"></span>' : '<span class="status-dot warn"></span>'}</div>
              </div>
              <div class="sampling-capabilities">${modeTags(row)} ${capabilityTags(row)}</div>
            </div>
            <div class="sampling-quick-stats">
              <div class="sampling-quick-stat"><span>温度</span><strong>${escapeHtml(summaryTemperature)}</strong></div>
              <div class="sampling-quick-stat"><span>推理强度</span><strong>${escapeHtml(summaryReasoning)}</strong></div>
              <div class="sampling-quick-stat"><span>思考预算</span><strong>${escapeHtml(summaryBudget)}</strong></div>
            </div>
          </summary>
          <div class="sampling-card-body">
            <div class="sampling-options">
              <label class="sampling-mode is-primary"><input type="checkbox" data-model-config-id="${row.id}" data-sampling-option="selected" />选择模型</label>
              ${search}
              ${reasoning}
            </div>
            ${officialDefaultMeta}
            <div class="sampling-runtime-block">
              <div class="sampling-runtime-title">本次运行配置</div>
              <div class="sampling-runtime-grid">
                ${versionPicker}
                <label class="sampling-subfield">
                  <span>显示版本备注</span>
                  <input
                    data-sampling-field="runtime_model_version"
                    data-model-config-id="${row.id}"
                    value="${escapeHtml(draft.runtime_model_version || "")}"
                    placeholder="如 2026-06 / 250615"
                  />
                </label>
                <label class="sampling-subfield">
                  <span>Temperature</span>
                  <input
                    data-sampling-field="temperature"
                    data-model-config-id="${row.id}"
                    type="number"
                    min="0"
                    max="2"
                    step="0.1"
                    value="${escapeHtml(draft.temperature || "")}"
                    placeholder="${escapeHtml(defaults.temperature || "模型默认")}"
                  />
                </label>
                <label class="sampling-subfield">
                  <span>推理强度</span>
                  <select data-sampling-field="reasoning_effort" data-model-config-id="${row.id}">
                    ${getReasoningChoices(row)}
                  </select>
                </label>
                <label class="sampling-subfield">
                  <span>思考预算</span>
                  <input
                    data-sampling-field="thinking_budget"
                    data-model-config-id="${row.id}"
                    type="number"
                    step="1"
                    value="${escapeHtml(draft.thinking_budget || "")}"
                    placeholder="${escapeHtml(defaults.thinking_budget || "0 / -1 / 1024")}"
                  />
                </label>
              </div>
              <p class="sampling-inline-help">${escapeHtml(samplingProviderNote(row))}</p>
            </div>
            ${searchDetailBlock}
          </div>
        </details>
      `;
    })
    .join("");
  for (const row of state.models.filter((item) => item.active)) {
    const draft = getSamplingDraft(row.id);
    const select = root.querySelector(`[data-sampling-field="reasoning_effort"][data-model-config-id="${row.id}"]`);
    if (select) select.value = draft.reasoning_effort || "";
  }
  updateSamplingSelectionSummary();
}

function updateSamplingSelectionSummary() {
  const summary = document.getElementById("samplingSelectionSummary");
  if (!summary) return;
  const selected = [...document.querySelectorAll('[data-sampling-option="selected"]:checked')];
  const searchCount = [...document.querySelectorAll('[data-sampling-option="search"]:checked')].length;
  const reasoningCount = [...document.querySelectorAll('[data-sampling-option="reasoning"]:checked')].length;
  if (!selected.length) {
    summary.textContent = "尚未选择模型";
    return;
  }
  const customVersionCount = selected.filter((input) => {
    const draft = getSamplingDraft(Number(input.dataset.modelConfigId));
    return Boolean((draft.runtime_model || "").trim());
  }).length;
  summary.textContent = `已选 ${selected.length} 个模型，其中联网搜索 ${searchCount} 个，深度思考 ${reasoningCount} 个，自定义运行版本 ${customVersionCount} 个`;
}

async function loadQuestions() {
  const filter = document.getElementById("questionFilterProjectSelect");
  const selected = filter?.value || currentProjectId() || "all";
  const query = selected === "all" ? "/api/questions" : `/api/questions?project_id=${selected}`;
  const data = await api(query);
  state.questions = data.questions;
  renderQuestions();
}

function renderQuestions() {
  const questionCount = state.questions.length;
  document.getElementById("questionStats").innerHTML = `<span class="tag">共 ${questionCount} 条</span>`;
  renderTable(
    document.getElementById("questionsTable"),
    [
      { key: "project", label: "所属项目", render: (row) => `<select data-question-select="project_id" data-id="${row.id}">${projectOptions(row.project_id)}</select>` },
      { key: "question", label: "问题内容", render: (row) => `<textarea data-question-field="question" data-id="${row.id}">${escapeHtml(row.question)}</textarea>` },
      { key: "question_type", label: "问题类型", render: (row) => `<input data-question-field="question_type" data-id="${row.id}" required value="${escapeHtml(row.question_type)}" />` },
      { key: "product_line", label: "产品线", render: (row) => `<input data-question-field="product_line" data-id="${row.id}" value="${escapeHtml(row.product_line || "")}" />` },
      { key: "purchase_stage", label: "采购阶段", render: (row) => `<input data-question-field="purchase_stage" data-id="${row.id}" value="${escapeHtml(row.purchase_stage || "")}" />` },
      { key: "scenario", label: "场景", render: (row) => `<input data-question-field="scenario" data-id="${row.id}" value="${escapeHtml(row.scenario || "")}" />` },
      { key: "priority", label: "优先级", render: (row) => `<input data-question-field="priority" data-id="${row.id}" value="${escapeHtml(row.priority)}" />` },
      { key: "suggested_platforms", label: "建议测试平台", render: (row) => `<input data-question-field="suggested_platforms" data-id="${row.id}" value="${escapeHtml(row.suggested_platforms || "")}" />` },
      { key: "notes", label: "备注", render: (row) => `<input data-question-field="notes" data-id="${row.id}" value="${escapeHtml(row.notes)}" />` },
      { key: "top30_pushed", label: "拜访前30题", render: (row) => `<input data-question-field="top30_pushed" data-id="${row.id}" value="${escapeHtml(row.top30_pushed || "")}" />` },
      { key: "first_screen_order", label: "首轮顺序", render: (row) => `<input data-question-field="first_screen_order" data-id="${row.id}" type="number" value="${escapeHtml(row.first_screen_order || 0)}" />` },
      { key: "filter_reason", label: "筛选理由", render: (row) => `<textarea data-question-field="filter_reason" data-id="${row.id}">${escapeHtml(row.filter_reason || "")}</textarea>` },
      {
        key: "actions",
        label: "操作",
        render: (row) => `
          <div class="question-row-actions">
            <span class="question-save-state" data-question-save-state="${row.id}">自动保存</span>
            <button class="ghost-btn delete-question-btn" data-id="${row.id}">删除</button>
          </div>
        `,
      },
    ],
    state.questions
  );
}

async function loadRuns() {
  const id = Number(document.getElementById("samplingProjectSelect").value || currentProjectId() || 0);
  if (!id) {
    updateSamplingHistoryHint(null);
    renderTable(document.getElementById("runsTable"), [{ label: "暂无项目" }], []);
    return;
  }
  const data = await api(`/api/runs?project_id=${id}`);
  updateSamplingHistoryHint(id, data.runs.length);
  renderTable(
    document.getElementById("runsTable"),
    [
      { key: "provider", label: "服务商" },
      { key: "model", label: "模型", render: (row) => escapeHtml(row.model_version ? `${row.model} / ${row.model_version}` : row.model) },
      { key: "search_enabled", label: "模式", render: (row) => (row.search_enabled ? "联网搜索" : "纯模型") },
      { key: "search_mode", label: "搜索策略", render: (row) => escapeHtml(row.search_mode || "off") },
      { key: "thinking_type", label: "思考模式", render: (row) => escapeHtml(row.thinking_type || "disabled") },
      { key: "reasoning_effort", label: "推理强度", render: (row) => escapeHtml(row.reasoning_effort || "-") },
      { key: "thinking_budget", label: "思考预算", render: (row) => escapeHtml(row.thinking_budget ?? "-") },
      { key: "requested_at", label: "生成时间", render: (row) => escapeHtml(formatDateTime(row.requested_at)) },
      { key: "question_type", label: "问题类型" },
      { key: "status", label: "状态" },
      { key: "recommendation_strength", label: "推荐强度" },
      { key: "target_brand_mentioned", label: "品牌命中", render: (row) => (row.target_brand_mentioned ? "是" : "否") },
      { key: "citations_json", label: "引用来源", render: (row) => renderCitationList(row) },
      { key: "response_text", label: "回答摘要", render: (row) => escapeHtml((row.response_text || "").slice(0, 180)) },
    ],
    data.runs
  );
}

async function loadAnalytics() {
  const id = Number(document.getElementById("analysisProjectSelect").value || currentProjectId() || 0);
  if (!id) {
    document.getElementById("metricCards").innerHTML = "";
    renderTable(document.getElementById("providerTable"), [{ label: "暂无项目" }], []);
    renderTable(document.getElementById("competitorTable"), [{ label: "暂无项目" }], []);
    return;
  }
  const data = await api(`/api/analytics?project_id=${id}`);
  document.getElementById("metricCards").innerHTML = `
    <div class="metric">总运行<strong>${data.total_runs}</strong></div>
    <div class="metric">成功运行<strong>${data.success_runs}</strong></div>
    <div class="metric">品牌命中率<strong>${data.brand_mention_rate}%</strong></div>
    <div class="metric">官网引用率<strong>${data.owned_citation_rate}%</strong></div>
  `;
  renderTable(
    document.getElementById("providerTable"),
    [
      { key: "provider", label: "模型 / 模式", render: (row) => escapeHtml(row.provider) },
      { key: "total", label: "运行数" },
      { key: "mentioned", label: "命中数" },
      { key: "mention_rate", label: "命中率" },
      { key: "owned_citation_rate", label: "官网引用率" },
    ],
    Object.entries(data.providers).map(([provider, value]) => ({ provider, ...value }))
  );
  renderTable(
    document.getElementById("competitorTable"),
    [
      { key: "name", label: "竞品" },
      { key: "count", label: "出现次数" },
    ],
    data.competitors
  );
}

function collectProjectRow(id) {
  const row = state.projects.find((item) => item.id === Number(id));
  const fields = ["client_name", "brand_name", "product_category", "target_region", "website_domain", "competitors"];
  const payload = { ...row };
  for (const field of fields) {
    const input = document.querySelector(`[data-field="${field}"][data-id="${id}"]`);
    payload[field] = input ? input.value : row[field];
  }
  return payload;
}

function collectModelRow(id) {
  const row = state.models.find((item) => item.id === Number(id));
  const payload = { ...row };
  for (const field of ["label", "provider", "api_family", "model", "model_version", "model_type", "api_base", "web_search_mode", "web_search_param_path", "reasoning_param_path", "reasoning_levels", "citation_param_path", "notes", "priority", "daily_limit"]) {
    const input = document.querySelector(`[data-model-field="${field}"][data-id="${id}"]`);
    payload[field] = input ? input.value : row[field];
  }
  const apiKeyInput = document.querySelector(`[data-model-field="api_key"][data-id="${id}"]`);
  payload.api_key = apiKeyInput && apiKeyInput.value ? apiKeyInput.value : "__KEEP__";
  for (const field of ["supports_pure", "supports_search", "supports_reasoning", "supports_citation", "supports_site_filter", "supports_time_filter", "supports_user_location", "supports_tool_calling", "active"]) {
    const input = document.querySelector(`[data-model-checkbox="${field}"][data-id="${id}"]`);
    payload[field] = input ? input.checked : Boolean(row[field]);
  }
  return payload;
}

function fillModelDetailForm(id) {
  const row = state.models.find((item) => item.id === Number(id));
  if (!row) return;
  const dialog = document.getElementById("modelDetailDialog");
  const form = document.getElementById("modelDetailForm");
  document.getElementById("modelDetailTitle").textContent = `${row.label} 详情设置`;
  document.getElementById("modelDetailSubtitle").textContent = `${row.provider} · ${row.model}`;
  for (const [name, value] of Object.entries(row)) {
    const input = form.elements.namedItem(name);
    if (!input) continue;
    if (input.type === "checkbox") {
      input.checked = Boolean(value);
    } else if (name !== "api_key") {
      input.value = value ?? "";
    }
  }
  form.elements.namedItem("id").value = row.id;
  form.elements.namedItem("api_key").value = "";
  form.elements.namedItem("api_key").placeholder = row.api_key_masked ? `当前：${row.api_key_masked}` : "留空则保留当前 Key";
  dialog.showModal();
}

function collectModelDetailPayload() {
  const form = document.getElementById("modelDetailForm");
  const id = Number(form.elements.namedItem("id").value);
  const row = state.models.find((item) => item.id === id);
  const payload = { ...row, id };
  const fields = [
    "label",
    "provider",
    "api_family",
    "model",
    "model_version",
    "model_type",
    "api_base",
    "priority",
    "daily_limit",
    "web_search_mode",
    "web_search_param_path",
    "reasoning_param_path",
    "reasoning_levels",
    "citation_param_path",
    "notes",
  ];
  for (const field of fields) {
    const input = form.elements.namedItem(field);
    payload[field] = input ? input.value : row[field];
  }
  const apiKeyInput = form.elements.namedItem("api_key");
  payload.api_key = apiKeyInput && apiKeyInput.value ? apiKeyInput.value : "__KEEP__";
  for (const field of ["supports_pure", "supports_search", "supports_reasoning", "supports_citation", "supports_site_filter", "supports_time_filter", "supports_user_location", "supports_tool_calling", "active"]) {
    const input = form.elements.namedItem(field);
    payload[field] = input ? input.checked : Boolean(row[field]);
  }
  return payload;
}

function collectQuestionRow(id) {
  const row = state.questions.find((item) => item.id === Number(id));
  const payload = { ...row };
  for (const field of ["question_id", "question_source", "question_type", "product_line", "purchase_stage", "scenario", "priority", "suggested_platforms", "question", "target_brand", "competitor_brands", "optimization_goal", "top30_pushed", "first_screen_order", "filter_reason", "notes"]) {
    const input = document.querySelector(`[data-question-field="${field}"][data-id="${id}"]`);
    payload[field] = input ? input.value : row[field];
  }
  const select = document.querySelector(`[data-question-select="project_id"][data-id="${id}"]`);
  payload.project_id = Number(select ? select.value : row.project_id);
  payload.industry = row.industry || "制造业";
  payload.product_category = row.product_category || "";
  payload.locale = row.locale || "zh-CN";
  if (!String(payload.question_type || "").trim()) {
    throw new Error("问题类型是必填项");
  }
  return payload;
}

function setQuestionSaveState(id, text, stateClass = "") {
  const el = document.querySelector(`[data-question-save-state="${id}"]`);
  if (!el) return;
  el.textContent = text;
  el.className = `question-save-state${stateClass ? ` ${stateClass}` : ""}`;
}

async function autosaveQuestion(id, { reload = false } = {}) {
  const numericId = Number(id);
  try {
    setQuestionSaveState(numericId, "保存中...");
    const payload = collectQuestionRow(numericId);
    await api("/api/questions/update", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const index = state.questions.findIndex((item) => item.id === numericId);
    if (index >= 0) state.questions[index] = { ...state.questions[index], ...payload };
    setQuestionSaveState(numericId, "已保存", "is-saved");
    if (reload) {
      await loadQuestions();
      await loadProjects();
      await loadRuns();
      await loadAnalytics();
    }
  } catch (error) {
    setQuestionSaveState(numericId, error.message, "is-error");
  }
}

function queueQuestionAutosave(id, delay = 500) {
  const numericId = Number(id);
  if (questionAutosaveTimers.has(numericId)) {
    clearTimeout(questionAutosaveTimers.get(numericId));
  }
  setQuestionSaveState(numericId, "待保存");
  const timer = window.setTimeout(async () => {
    questionAutosaveTimers.delete(numericId);
    await autosaveQuestion(numericId);
  }, delay);
  questionAutosaveTimers.set(numericId, timer);
}

function downloadWorkbook(filename, rows) {
  if (!window.XLSX) throw new Error("Excel 导出库未加载");
  const wb = window.XLSX.utils.book_new();
  const ws = window.XLSX.utils.json_to_sheet(rows);
  window.XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
  const output = window.XLSX.write(wb, {
    bookType: "xlsx",
    type: "array",
  });
  const blob = new Blob([output], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  if (window.navigator?.msSaveOrOpenBlob) {
    window.navigator.msSaveOrOpenBlob(blob, filename);
    return;
  }
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  window.setTimeout(() => {
    link.remove();
    window.URL.revokeObjectURL(url);
  }, 1000);
}

async function exportRunsWorkbook(projectId) {
  if (!projectId) return alert("请先选择项目");
  const data = await api(`/api/runs?project_id=${projectId}`);
  const rows = data.runs.map((row) => ({
    运行ID: row.run_id,
    批次ID: row.batch_id,
    服务商: row.provider,
    模型: row.model,
    模式: row.search_enabled ? "联网搜索" : "纯模型",
    搜索策略: row.search_mode || "off",
    思考模式: row.thinking_type || "disabled",
    推理强度: row.reasoning_effort || "",
    思考预算: row.thinking_budget ?? "",
    问题类型: row.question_type,
    问题: row.question,
    状态: row.status,
    推荐强度: row.recommendation_strength,
    品牌命中: row.target_brand_mentioned ? "是" : "否",
    引用来源: parseCitations(row.citations_json).map((item) => item.title || item.url || "").filter(Boolean).join(" | "),
    回答摘要: row.response_text,
    错误信息: row.error_message,
    生成时间: formatDateTime(row.requested_at),
  }));
  downloadWorkbook("geo-runs.xlsx", rows);
}

async function exportSummaryWorkbook(projectId) {
  if (!projectId) return alert("请先选择项目");
  const data = await api(`/api/analytics?project_id=${projectId}`);
  const rows = [
    { 指标: "总运行", 值: data.total_runs },
    { 指标: "成功运行", 值: data.success_runs },
    { 指标: "品牌命中率", 值: `${data.brand_mention_rate}%` },
    { 指标: "官网引用率", 值: `${data.owned_citation_rate}%` },
    ...Object.entries(data.providers).map(([provider, value]) => ({
      指标: provider,
      值: `运行 ${value.total} / 命中率 ${value.mention_rate}% / 官网引用率 ${value.owned_citation_rate}%`,
    })),
  ];
  downloadWorkbook("geo-summary.xlsx", rows);
}

function downloadQuestionTemplate() {
  downloadWorkbook("问题库模板.xlsx", QUESTION_TEMPLATE_ROWS);
}

async function readSpreadsheet(file) {
  if (!window.XLSX) throw new Error("Excel 解析库未加载");
  const buffer = await file.arrayBuffer();
  const workbook = window.XLSX.read(buffer, { type: "array" });
  const sheet = workbook.Sheets[workbook.SheetNames[0]];
  return window.XLSX.utils.sheet_to_json(sheet, { defval: "" });
}

async function refreshAll() {
  await loadProjects();
  await loadModels();
  await loadQuestions();
  await loadRuns();
  await loadAnalytics();
}

document.querySelectorAll("[data-page-link]").forEach((link) => {
  link.addEventListener("click", () => setPage(link.dataset.pageLink));
});

window.addEventListener("hashchange", initRouting);

document.getElementById("toggleProjectCreateBtn").addEventListener("click", () => {
  document.getElementById("projectCreateForm").classList.toggle("hidden");
});

document.getElementById("cancelProjectCreateBtn").addEventListener("click", () => {
  document.getElementById("projectCreateForm").classList.add("hidden");
});

document.getElementById("projectCreateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = await api("/api/projects", { method: "POST", body: JSON.stringify(parseForm(event.target)) });
  state.selectedProjectId = data.id;
  event.target.reset();
  projectCompetitorTags.sync("竞品A;竞品B");
  document.getElementById("projectCreateForm").classList.add("hidden");
  await refreshAll();
});

document.getElementById("projectsTable").addEventListener("click", async (event) => {
  const target = event.target;
  if (target.classList.contains("project-competitor-remove")) {
    const hidden = document.querySelector(`[data-field="competitors"][data-id="${target.dataset.id}"]`);
    const items = parseSemicolonList(hidden.value);
    items.splice(Number(target.dataset.index), 1);
    hidden.value = stringifySemicolonList(items);
    const row = state.projects.find((item) => item.id === Number(target.dataset.id));
    if (row) row.competitors = hidden.value;
    renderProjects();
    return;
  }
  if (target.classList.contains("select-project-btn")) {
    state.selectedProjectId = Number(target.dataset.id);
    syncProjectSelectors();
    renderProjects();
    setExportLinks();
    await loadQuestions();
    await loadRuns();
    await loadAnalytics();
  }
  if (target.classList.contains("save-project-btn")) {
    await api("/api/projects/update", {
      method: "POST",
      body: JSON.stringify(collectProjectRow(target.dataset.id)),
    });
    await refreshAll();
  }
  if (target.classList.contains("delete-project-btn")) {
    const row = state.projects.find((item) => item.id === Number(target.dataset.id));
    const label = row ? `${row.client_name} / ${row.brand_name}` : "该项目";
    if (!confirm(`确认删除「${label}」？相关问题和采样记录也会一并删除。`)) return;
    await api("/api/projects/delete", {
      method: "POST",
      body: JSON.stringify({ id: Number(target.dataset.id) }),
    });
    if (Number(state.selectedProjectId) === Number(target.dataset.id)) {
      state.selectedProjectId = null;
    }
    await refreshAll();
  }
});

document.getElementById("projectsTable").addEventListener("keydown", (event) => {
  const target = event.target;
  if (!target.matches("[data-competitor-option-input]")) return;
  if (!["Enter", ",", "，"].includes(event.key)) return;
  event.preventDefault();
  const value = target.value.trim();
  if (!value) return;
  const id = target.dataset.competitorOptionInput;
  const hidden = document.querySelector(`[data-field="competitors"][data-id="${id}"]`);
  const items = parseSemicolonList(hidden.value);
  if (!items.includes(value)) items.push(value);
  hidden.value = stringifySemicolonList(items);
  const row = state.projects.find((item) => item.id === Number(id));
  if (row) row.competitors = hidden.value;
  renderProjects();
});

document.getElementById("projectsTable").addEventListener("focusout", (event) => {
  const target = event.target;
  if (!target.matches("[data-competitor-option-input]")) return;
  const value = target.value.trim();
  if (!value) return;
  const id = target.dataset.competitorOptionInput;
  const hidden = document.querySelector(`[data-field="competitors"][data-id="${id}"]`);
  const items = parseSemicolonList(hidden.value);
  if (!items.includes(value)) items.push(value);
  hidden.value = stringifySemicolonList(items);
  const row = state.projects.find((item) => item.id === Number(id));
  if (row) row.competitors = hidden.value;
  renderProjects();
});

document.getElementById("activeProjectSelect").addEventListener("change", async (event) => {
  state.selectedProjectId = Number(event.target.value);
  syncProjectSelectors();
  setExportLinks();
  renderProjects();
  await loadQuestions();
  await loadRuns();
  await loadAnalytics();
});

document.getElementById("questionFilterProjectSelect").addEventListener("change", loadQuestions);
document.getElementById("downloadQuestionTemplateBtn").addEventListener("click", downloadQuestionTemplate);
document.getElementById("downloadQuestionTemplateCardBtn").addEventListener("click", downloadQuestionTemplate);

document.getElementById("seedBtn").addEventListener("click", async () => {
  const project_id = Number(document.getElementById("questionImportProjectSelect").value || currentProjectId());
  if (!project_id) return alert("请先创建项目");
  const data = await api("/api/questions/seed", { method: "POST", body: JSON.stringify({ project_id }) });
  alert(`已生成 ${data.count} 个模板问题`);
  await refreshAll();
});

document.getElementById("importCsvBtn").addEventListener("click", async () => {
  const project_id = Number(document.getElementById("questionImportProjectSelect").value || currentProjectId());
  if (!project_id) return alert("请先选择项目");
  const data = await api("/api/questions/import", {
    method: "POST",
    body: JSON.stringify({ project_id, csv_text: document.getElementById("csvInput").value }),
  });
  alert(`已导入 ${data.count} 个问题`);
  await refreshAll();
});

document.getElementById("importFileBtn").addEventListener("click", async () => {
  const input = document.getElementById("questionFileInput");
  const file = input.files[0];
  const project_id = Number(document.getElementById("questionImportProjectSelect").value || currentProjectId());
  if (!project_id) return alert("请先选择项目");
  if (!file) return alert("请选择文件");
  try {
    if (file.name.toLowerCase().endsWith(".csv")) {
      const csvText = await file.text();
      await api("/api/questions/import", {
        method: "POST",
        body: JSON.stringify({ project_id, csv_text: csvText }),
      });
    } else {
      const rows = await readSpreadsheet(file);
      await api("/api/questions/import_rows", {
        method: "POST",
        body: JSON.stringify({ project_id, rows }),
      });
    }
    input.value = "";
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("questionsTable").addEventListener("click", async (event) => {
  const target = event.target;
  if (target.classList.contains("delete-question-btn")) {
    const row = state.questions.find((item) => item.id === Number(target.dataset.id));
    const preview = row?.question ? row.question.slice(0, 30) : "该问题";
    if (!window.confirm(`确认删除问题「${preview}」？关联采样记录也会一并删除。`)) return;
    await api("/api/questions/delete", {
      method: "POST",
      body: JSON.stringify({ id: Number(target.dataset.id) }),
    });
    await loadQuestions();
    await loadProjects();
    await loadRuns();
    await loadAnalytics();
  }
});

document.getElementById("questionsTable").addEventListener("input", (event) => {
  const target = event.target;
  if (!target.matches("[data-question-field]")) return;
  queueQuestionAutosave(target.dataset.id, target.tagName === "TEXTAREA" ? 700 : 500);
});

document.getElementById("questionsTable").addEventListener("change", async (event) => {
  const target = event.target;
  if (target.matches("[data-question-select]")) {
    await autosaveQuestion(target.dataset.id, { reload: true });
    return;
  }
  if (target.matches("[data-question-field]")) {
    await autosaveQuestion(target.dataset.id);
  }
});

document.getElementById("questionsTable").addEventListener("focusout", async (event) => {
  const target = event.target;
  if (!target.matches("[data-question-field]")) return;
  const id = Number(target.dataset.id);
  if (questionAutosaveTimers.has(id)) {
    clearTimeout(questionAutosaveTimers.get(id));
    questionAutosaveTimers.delete(id);
    await autosaveQuestion(id);
  }
});

document.getElementById("toggleModelCreateBtn").addEventListener("click", () => {
  document.getElementById("modelCreateForm").classList.toggle("hidden");
});

document.getElementById("cancelModelCreateBtn").addEventListener("click", () => {
  document.getElementById("modelCreateForm").classList.add("hidden");
});

document.getElementById("modelCreateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await api("/api/models", {
    method: "POST",
    body: JSON.stringify(parseForm(event.target)),
  });
  event.target.reset();
  document.getElementById("modelCreateForm").classList.add("hidden");
  await loadModels();
});

document.getElementById("modelsTable").addEventListener("click", async (event) => {
  const target = event.target;
  if (target.classList.contains("model-detail-btn")) {
    fillModelDetailForm(target.dataset.id);
    return;
  }
  if (target.classList.contains("test-model-btn")) {
    const payload = collectModelRow(target.dataset.id);
    const result = await api("/api/models/test", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    alert(`测试成功：${result.model}，耗时 ${result.latency_ms} ms\n${result.preview}`);
    return;
  }
  if (target.classList.contains("save-model-btn")) {
    await api("/api/models/update", {
      method: "POST",
      body: JSON.stringify(collectModelRow(target.dataset.id)),
    });
    await loadModels();
  }
  if (target.classList.contains("delete-model-btn")) {
    if (!window.confirm("确定删除这个模型配置吗？")) return;
    await api("/api/models/delete", {
      method: "POST",
      body: JSON.stringify({ id: Number(target.dataset.id) }),
    });
    await loadModels();
  }
});

document.getElementById("closeModelDetailBtn").addEventListener("click", () => {
  document.getElementById("modelDetailDialog").close();
});

document.getElementById("testModelFromDialogBtn").addEventListener("click", async () => {
  const payload = collectModelDetailPayload();
  const result = await api("/api/models/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  alert(`测试成功：${result.model}，耗时 ${result.latency_ms} ms\n${result.preview}`);
});

document.getElementById("saveModelDetailBtn").addEventListener("click", async () => {
  await api("/api/models/update", {
    method: "POST",
    body: JSON.stringify(collectModelDetailPayload()),
  });
  document.getElementById("modelDetailDialog").close();
  await loadModels();
});

document.getElementById("modelPresetButtons").addEventListener("click", (event) => {
  const target = event.target;
  if (!target.classList.contains("model-preset-btn")) return;
  const preset = state.presets[target.dataset.provider];
  if (!preset) return;
  const form = document.getElementById("modelCreateForm");
  form.classList.remove("hidden");
  for (const [name, value] of Object.entries(preset)) {
    const input = form.elements.namedItem(name);
    if (!input) continue;
    if (input.type === "checkbox") {
      input.checked = Boolean(value);
    } else {
      input.value = value ?? "";
    }
  }
  form.elements.namedItem("api_key").value = "";
  form.elements.namedItem("priority").value = preset.priority || 100;
  form.elements.namedItem("daily_limit").value = preset.daily_limit || 0;
});

document.getElementById("samplingProjectSelect").addEventListener("change", async (event) => {
  state.selectedProjectId = Number(event.target.value);
  syncProjectSelectors();
  setExportLinks();
  renderProjects();
  await loadRuns();
  await loadAnalytics();
});

document.getElementById("analysisProjectSelect").addEventListener("change", async (event) => {
  state.selectedProjectId = Number(event.target.value);
  syncProjectSelectors();
  setExportLinks();
  renderProjects();
  await loadAnalytics();
  await loadRuns();
});

function syncSamplingDraftField(target) {
  if (!target.matches("[data-sampling-field]")) return false;
  const modelId = Number(target.dataset.modelConfigId);
  const field = target.dataset.samplingField;
  getSamplingDraft(modelId)[field] = target.type === "checkbox" ? target.checked : target.value;
  updateSamplingSelectionSummary();
  return true;
}

document.getElementById("samplingModelList").addEventListener("input", (event) => {
  syncSamplingDraftField(event.target);
});

document.getElementById("samplingModelList").addEventListener("toggle", (event) => {
  const target = event.target;
  if (!target.matches("[data-sampling-card]")) return;
  const modelId = Number(target.dataset.samplingCard);
  getSamplingDraft(modelId).expanded = target.open;
});

document.getElementById("samplingModelList").addEventListener("change", (event) => {
  const target = event.target;
  if (syncSamplingDraftField(target)) {
    return;
  }
  if (!target.matches("[data-sampling-option]")) return;
  const modelId = target.dataset.modelConfigId;
  const selected = document.querySelector(`[data-sampling-option="selected"][data-model-config-id="${modelId}"]`);
  const search = document.querySelector(`[data-sampling-option="search"][data-model-config-id="${modelId}"]`);
  const reasoning = document.querySelector(`[data-sampling-option="reasoning"][data-model-config-id="${modelId}"]`);
  const row = state.models.find((item) => String(item.id) === String(modelId));
  if (target.dataset.samplingOption === "selected") {
    const enabled = target.checked;
    const card = document.querySelector(`[data-sampling-card="${modelId}"]`);
    if (card) card.open = enabled || getSamplingDraft(modelId).expanded;
    if (search) {
      search.disabled = !enabled;
      if (!enabled) search.checked = false;
    }
    if (reasoning) {
      reasoning.disabled = !enabled;
      if (!enabled) reasoning.checked = false;
    }
  } else if (target.checked && selected && !selected.checked) {
    selected.checked = true;
    if (search) search.disabled = false;
    if (reasoning) reasoning.disabled = false;
  }
  if (row?.provider === "kimi") {
    if (target.dataset.samplingOption === "search" && target.checked && reasoning) {
      reasoning.checked = false;
      reasoning.disabled = true;
    }
    if (target.dataset.samplingOption === "search" && !target.checked && reasoning && selected?.checked) {
      reasoning.disabled = false;
    }
    if (target.dataset.samplingOption === "reasoning" && target.checked && search) {
      search.checked = false;
    }
  }
  updateSamplingSelectionSummary();
});

document.getElementById("startRunBtn").addEventListener("click", async () => {
  try {
    const runStatus = document.getElementById("runStatus");
    runStatus.textContent = "准备采样...";
    setSamplingProgress({ label: "准备采样", percent: 0, detail: "0%" });
    const project_id = Number(document.getElementById("samplingProjectSelect").value || currentProjectId());
    if (!project_id) {
      runStatus.textContent = "请先选择项目";
      alert("请先选择项目");
      return;
    }
    const selectedInputs = [...document.querySelectorAll('[data-sampling-option="selected"]:checked')];
    if (!selectedInputs.length) {
      runStatus.textContent = "请至少选择一个模型";
      alert("请至少选择一个模型");
      return;
    }
    const models = selectedInputs.map((input) => {
      const modelId = Number(input.dataset.modelConfigId);
      const search = document.querySelector(`[data-sampling-option="search"][data-model-config-id="${modelId}"]`);
      const reasoning = document.querySelector(`[data-sampling-option="reasoning"][data-model-config-id="${modelId}"]`);
      const draft = getSamplingDraft(modelId);
      const row = state.models.find((item) => item.id === modelId);
      if (!row) {
        throw new Error(`未找到模型配置：${modelId}`);
      }
      const defaults = getProviderSamplingDefaults(row);
      const temperature = String(draft.temperature || defaults.temperature || "").trim();
      const thinkingBudgetRaw = String(draft.thinking_budget || defaults.thinking_budget || "").trim();
      return {
        model_config_id: modelId,
        search_enabled: Boolean(search?.checked),
        thinking_enabled: Boolean(reasoning?.checked),
        thinking_type: reasoning?.checked ? "enabled" : "disabled",
        search_mode: search?.checked ? "auto" : "off",
        temperature: temperature === "" ? null : Number(temperature),
        reasoning_effort: String(draft.reasoning_effort || defaults.reasoning_effort || "").trim(),
        thinking_budget: thinkingBudgetRaw === "" ? null : Number(thinkingBudgetRaw),
        runtime_model: String(draft.runtime_model || row.model || "").trim(),
        runtime_model_version: String(draft.runtime_model_version || "").trim(),
        search_sources: String(draft.search_sources || "").trim(),
        search_limit: draft.search_limit === "" ? null : Number(draft.search_limit),
        search_max_keyword: String(draft.search_max_keyword || "").trim(),
        search_user_location: String(draft.search_user_location || "").trim(),
        search_site_filter: String(draft.search_site_filter || "").trim(),
        search_time_filter: String(draft.search_time_filter || "").trim(),
        search_strategy: String(draft.search_strategy || "").trim(),
        search_freshness: String(draft.search_freshness || "").trim(),
        search_prompt_intervene: String(draft.search_prompt_intervene || "").trim(),
        search_enable_source: Boolean(draft.search_enable_source),
        search_enable_citation: Boolean(draft.search_enable_citation),
        search_citation_format: String(draft.search_citation_format || "").trim(),
      };
    });
    runStatus.textContent = `采样中...（${models.length} 个模型）`;
    setSamplingProgress({ label: "创建采样任务", percent: 2, detail: `0 / ${models.length}` });
    const result = await api("/api/runs/start", {
      method: "POST",
      body: JSON.stringify({
        project_id,
        models,
        repeat_count: Number(document.getElementById("repeatCount").value || 1),
      }),
    });
    runStatus.textContent = "排队中";
    setSamplingProgress({ label: "采样任务已创建", percent: 4, detail: `0 / ${result.total || 0}` });
    startSamplingJobPolling(result.batch_id, project_id);
    await pollSamplingJob(result.batch_id, project_id);
  } catch (error) {
    document.getElementById("runStatus").textContent = "采样失败";
    console.error("startRun failed", error);
    alert(error.message);
  }
});

document.getElementById("exportRunsFromHistory").addEventListener("click", (event) => {
  const projectId = Number(event.currentTarget.dataset.projectId || currentProjectId());
  setExportFeedback("");
  if (projectId) return;
  event.preventDefault();
  alert("请先选择项目");
});

document.getElementById("exportSummary").addEventListener("click", (event) => {
  const projectId = Number(event.currentTarget.dataset.projectId || currentProjectId());
  setExportFeedback("");
  if (projectId) return;
  event.preventDefault();
  alert("请先选择项目");
});

const projectCompetitorTags = initTagInput("projectCompetitorInput", "projectCompetitorsHidden");

document.getElementById("authLoginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("authPasswordInput");
  const message = document.getElementById("authMessage");
  if (message) message.textContent = "";
  try {
    await authRequest("/api/auth/login", { password: input.value });
    input.value = "";
    hideAuthGate();
    await refreshAll();
  } catch (error) {
    if (message) message.textContent = error.message;
  }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  try {
    await authRequest("/api/auth/logout", {});
  } finally {
    showAuthGate("已退出");
  }
});

async function boot() {
  initRouting();
  const status = await loadAuthStatus();
  if (status.auth_enabled && !status.authenticated) {
    showAuthGate("");
    return;
  }
  hideAuthGate();
  await refreshAll();
}

boot().catch((error) => {
  const runStatus = document.getElementById("runStatus");
  if (runStatus) runStatus.textContent = "加载失败";
  console.error(error);
  alert(error.message);
});
