import type { ReactNode } from "react";
import { CheckCircle2, CircleAlert, Clock3, Loader2 } from "lucide-react";

export function StatusBadge({ status }: { status?: string }) {
  const value = status || "unknown";
  const icon = value === "completed" ? <CheckCircle2 size={14} /> : value === "failed" ? <CircleAlert size={14} /> : value === "running" ? <Loader2 size={14} /> : <Clock3 size={14} />;
  return <span className={`status-badge status-${value}`}>{icon}{statusLabel(value)}</span>;
}

export function statusLabel(status: string) {
  return ({ queued: "排队", running: "运行中", pause_requested: "暂停中", paused: "已暂停", completed: "完成", failed: "失败", unknown: "未知" } as Record<string, string>)[status] || status;
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
