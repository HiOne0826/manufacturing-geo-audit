import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { BarChart3, Boxes, ChevronRight, Gauge, Layers3, ListChecks, Menu, PanelLeftClose, Play, Settings, SlidersHorizontal, X } from "lucide-react";
import { authApi, projectsApi, tasksApi } from "../api/resources";
import type { SamplingBatch } from "../api/types";
import { useSelectionStore } from "../store/selectionStore";
import { queryClient } from "../app/queryClient";
import { AsyncBoundary, useDialogFocus } from "./ui";
import { StatusBadge } from "./common";

const navGroups = [
  { label: "工作台", items: [{ to: "/", label: "总览", icon: Gauge }, { to: "/batches", label: "全部批次", icon: Layers3 }] },
  { label: "当前项目", items: [{ to: "/projects", label: "项目", icon: Boxes }, { to: "/questions", label: "问题库", icon: ListChecks }, { to: "/sampling", label: "采样", icon: Play }, { to: "/analysis", label: "分析", icon: BarChart3 }] },
  { label: "系统管理", items: [{ to: "/models", label: "模型", icon: SlidersHorizontal }, { to: "/settings", label: "设置", icon: Settings }] }
];

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileNav, setMobileNav] = useState(false);
  const [taskCenter, setTaskCenter] = useState(false);
  const mobileNavRef = useRef<HTMLElement>(null);
  const location = useLocation();
  const projectId = useSelectionStore((state) => state.projectId);
  const showProjectTopbar = !["/models", "/settings"].includes(location.pathname);
  useEffect(() => { setMobileNav(false); }, [location.pathname]);
  const logout = useMutation({ mutationFn: authApi.logout, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth"] }) });
  useDialogFocus(mobileNav, mobileNavRef, () => setMobileNav(false));
  return (
    <div className="app-shell">
      <button className="mobile-nav-trigger ghost" aria-label="打开导航" onClick={() => setMobileNav(true)}><Menu size={19} /></button>
      {mobileNav ? <button className="mobile-nav-scrim" aria-label="关闭导航" onClick={() => setMobileNav(false)} /> : null}
      <aside ref={mobileNavRef} className={`sidebar ${mobileNav ? "is-open" : ""}`} role={mobileNav ? "dialog" : undefined} aria-modal={mobileNav ? "true" : undefined} aria-label={mobileNav ? "主导航" : undefined}>
        <div className="brand-block"><img className="brand-logo" src={`${import.meta.env.BASE_URL}brand/ostrich-brand-logo.png`} alt="" /><div><strong>鸵鸟 GEO</strong><span>审计工作台</span></div><button className="mobile-close ghost" aria-label="关闭导航" onClick={() => setMobileNav(false)}><X size={18} /></button></div>
        <nav aria-label="主导航">{navGroups.map((group) => <section className="nav-group" key={group.label}><h2>{group.label}</h2>{group.items.map((item) => <NavLink key={item.to} to={item.to} end={item.to === "/"}><item.icon size={17} /><span>{item.label}</span></NavLink>)}</section>)}</nav>
        <div className="sidebar-foot"><span>V2 · 内部交付环境</span><button className="ghost" onClick={() => logout.mutate()} disabled={logout.isPending}>退出</button></div>
      </aside>
      <section className="workspace">
        {showProjectTopbar ? <header className="topbar">
          <ProjectContextBar />
          <button className="task-center-trigger ghost" onClick={() => setTaskCenter(true)}><PanelLeftClose size={17} />任务中心</button>
        </header> : null}
        <div className="route-stage" key={projectId || "no-project"}>{children}</div>
      </section>
      <TaskCenter open={taskCenter} onClose={() => setTaskCenter(false)} />
    </div>
  );
}

function ProjectContextBar() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const { projectId, setProjectId } = useSelectionStore();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const activeProjects = useMemo(() => (projects.data?.projects || []).filter((project) => !project.archived_at), [projects.data]);
  const validIds = useMemo(() => new Set(activeProjects.map((project) => project.id)), [activeProjects]);

  useEffect(() => {
    if (!projects.data) return;
    const rawQueryId = searchParams.get("project_id");
    const queryId = Number(rawQueryId) || null;
    const invalidDeepLink = Boolean(rawQueryId) && (!queryId || !validIds.has(queryId));
    const nextId = invalidDeepLink ? null : queryId && validIds.has(queryId) ? queryId : projectId && validIds.has(projectId) ? projectId : null;
    if (nextId !== projectId) setProjectId(nextId);
    if ((nextId && queryId !== nextId) || invalidDeepLink) {
      const next = new URLSearchParams(searchParams);
      if (nextId) next.set("project_id", String(nextId)); else next.delete("project_id");
      setSearchParams(next, { replace: true });
    }
  }, [activeProjects, projectId, projects.data, searchParams, setProjectId, setSearchParams, validIds]);

  const changeProject = (nextId: number | null) => {
    if (nextId === projectId) return;
    if (document.querySelector('[data-dirty="true"]') && !window.confirm("当前页面有未保存内容。切换项目会丢失这些内容，是否继续？")) return;
    const previousId = projectId;
    setProjectId(nextId);
    const next = new URLSearchParams(searchParams);
    if (nextId) next.set("project_id", String(nextId)); else next.delete("project_id");
    next.delete("questions_page");
    next.delete("batches_page");
    next.delete("failed_page");
    next.delete("results_page");
    next.delete("attempts_page");
    next.delete("batch_id");
    next.delete("baseline_batch_id");
    next.delete("comparison_batch_id");
    setSearchParams(next, { replace: true });
    queryClient.cancelQueries();
    if (previousId) {
      queryClient.removeQueries({ queryKey: ["questions", previousId] });
      queryClient.removeQueries({ queryKey: ["batches", previousId] });
      queryClient.removeQueries({ queryKey: ["analytics", previousId] });
      queryClient.removeQueries({ queryKey: ["analytics-summary", previousId] });
    }
    if (/^\/batches\//.test(location.pathname)) navigate(`/batches?project_id=${nextId || ""}`);
  };

  const current = projects.data?.projects.find((project) => project.id === projectId);
  return <div className="project-context"><div className="context-label"><span>当前项目</span>{current ? <strong>{current.brand_name}</strong> : null}</div><select aria-label="切换当前项目" value={projectId || ""} disabled={projects.isLoading} onChange={(event) => changeProject(Number(event.target.value) || null)}><option value="">选择项目</option>{activeProjects.map((project) => <option key={project.id} value={project.id}>{project.client_name} / {project.brand_name}</option>)}</select>{current ? <span className="context-meta">{current.product_category || "未设置品类"}</span> : null}</div>;
}

function TaskCenter({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { setProjectId } = useSelectionStore();
  const panelRef = useRef<HTMLElement>(null);
  const batches = useQuery({ queryKey: ["tasks", "active"], queryFn: tasksApi.active, refetchInterval: open ? 2500 : false });
  const active = (batches.data?.batches || []).filter((item) => ["queued", "running", "pause_requested", "paused", "failed", "failed_system"].includes(item.status));
  useDialogFocus(open, panelRef, onClose);
  if (!open) return null;
  const openBatch = (batch: SamplingBatch) => { setProjectId(batch.project_id); onClose(); navigate(`/batches/${batch.batch_id}?project_id=${batch.project_id}`); };
  return <><button className="drawer-scrim" aria-label="关闭任务中心" onClick={onClose} /><aside ref={panelRef} className="task-drawer" role="dialog" aria-modal="true" aria-labelledby="task-center-title"><header><div><span>实时任务</span><h2 id="task-center-title">任务中心</h2></div><button className="ghost icon-button" aria-label="关闭任务中心" onClick={onClose}><X size={18} /></button></header><AsyncBoundary loading={batches.isLoading} refreshing={batches.isFetching && !batches.isLoading} stale={batches.isError && Boolean(batches.data)} error={batches.data ? null : batches.error} empty={!active.length} emptyLabel="当前没有运行或异常批次" onRetry={() => batches.refetch()}><div className="task-list">{active.map((batch) => <button className="task-item" key={batch.batch_id} onClick={() => openBatch(batch)}><div><strong>{batch.batch_name || batch.batch_id}</strong><span>{batch.batch_name ? batch.batch_id : `项目 #${batch.project_id}`}</span></div><StatusBadge status={batch.status} /><ChevronRight size={16} /></button>)}</div></AsyncBoundary></aside></>;
}
