import { useEffect, useId, useState, type ReactNode } from "react";
import { CheckCircle2, CircleAlert, Clock3, Loader2 } from "lucide-react";

export function StatusBadge({ status }: { status?: string }) {
  const value = status || "unknown";
  const icon = value === "completed" ? <CheckCircle2 size={14} /> : value === "failed" || value === "failed_system" ? <CircleAlert size={14} /> : value === "running" ? <Loader2 size={14} /> : <Clock3 size={14} />;
  return <span className={`status-badge status-${value}`}>{icon}{statusLabel(value)}</span>;
}

export function statusLabel(status: string) {
  return ({ queued: "排队", running: "运行中", pause_requested: "暂停中", paused: "已暂停", completed: "完成", failed: "有失败", failed_system: "系统故障", cancelled: "已取消", active: "进行中", draft: "草稿", reviewed: "已审核", frozen: "已冻结", archived: "已归档", valid: "有效", excluded: "已排除", needs_review: "需复核", unknown: "未知" } as Record<string, string>)[status] || status;
}

export function Metric({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong>{hint ? <em>{hint}</em> : null}</div>;
}

export function EmptyState({ title }: { title: string }) {
  return <div className="empty-state">{title}</div>;
}

export function PageTitle({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="page-title"><div><h1>{title}</h1><p>{description}</p></div>{action}</div>;
}

export function Pagination({ page, totalItems, pageSize = 10, onChange }: { page: number; totalItems: number; pageSize?: number; onChange: (page: number) => void }) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const currentPage = Math.min(Math.max(page, 1), totalPages);
  const jumpInputId = useId();
  const [jumpValue, setJumpValue] = useState(String(currentPage));
  useEffect(() => setJumpValue(String(currentPage)), [currentPage]);
  if (totalItems <= pageSize) return null;
  const firstItem = (currentPage - 1) * pageSize + 1;
  const lastItem = Math.min(currentPage * pageSize, totalItems);
  const pages = paginationPages(currentPage, totalPages);
  const jump = () => {
    const parsed = Number.parseInt(jumpValue, 10);
    if (!Number.isFinite(parsed)) {
      setJumpValue(String(currentPage));
      return;
    }
    onChange(Math.min(Math.max(parsed, 1), totalPages));
  };
  return (
    <nav className="pagination" aria-label="分页导航">
      <span className="pagination-summary">第 {firstItem}–{lastItem} 条，共 {totalItems} 条</span>
      <div className="pagination-buttons">
        <button type="button" className="ghost" disabled={currentPage === 1} onClick={() => onChange(currentPage - 1)}>上一页</button>
        {pages.map((item, index) => item === "ellipsis"
          ? <span className="pagination-ellipsis" aria-hidden="true" key={`ellipsis-${index}`}>…</span>
          : <button type="button" className={`pagination-page ${item === currentPage ? "is-current" : "ghost"} ${Math.abs(item - currentPage) <= 1 ? "is-neighbor" : ""}`} aria-label={`第 ${item} 页`} aria-current={item === currentPage ? "page" : undefined} key={item} onClick={() => onChange(item)}>{item}</button>)}
        <button type="button" className="ghost" disabled={currentPage === totalPages} onClick={() => onChange(currentPage + 1)}>下一页</button>
      </div>
      <form className="pagination-jump" onSubmit={(event) => { event.preventDefault(); jump(); }}>
        <label htmlFor={jumpInputId}>跳至</label>
        <input id={jumpInputId} inputMode="numeric" pattern="[0-9]*" aria-label="跳转页码" value={jumpValue} onChange={(event) => setJumpValue(event.target.value)} />
        <span>页</span>
        <button type="submit" className="ghost">跳转</button>
      </form>
    </nav>
  );
}

function paginationPages(current: number, total: number): Array<number | "ellipsis"> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const candidates = new Set([1, total, current - 1, current, current + 1].filter((page) => page >= 1 && page <= total));
  const ordered = [...candidates].sort((a, b) => a - b);
  const result: Array<number | "ellipsis"> = [];
  ordered.forEach((page, index) => {
    if (index > 0 && page - ordered[index - 1] > 1) result.push("ellipsis");
    result.push(page);
  });
  return result;
}
