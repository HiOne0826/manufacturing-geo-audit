import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { AlertTriangle, CheckCircle2, Loader2, X } from "lucide-react";

export function AsyncBoundary({ loading, refreshing, stale, error, empty, emptyLabel = "暂无数据", loadingLabel = "正在加载…", children, onRetry }: {
  loading?: boolean; refreshing?: boolean; stale?: boolean; error?: Error | null; empty?: boolean; emptyLabel?: string; loadingLabel?: string; children?: ReactNode; onRetry?: () => void;
}) {
  if (loading) return <div className="async-state async-skeleton" role="status"><span className="sr-only">{loadingLabel}</span><i /><i /><i /></div>;
  if (error) return <div className="async-state is-error" role="alert"><AlertTriangle size={18} /><div><strong>加载失败</strong><span>{error.message}</span></div>{onRetry ? <button className="ghost" onClick={onRetry}>重试</button> : null}</div>;
  if (empty) return <div className="async-state is-empty"><span>{emptyLabel}</span></div>;
  return <>{refreshing ? <div className="refresh-indicator" role="status"><Loader2 className="spin" size={14} />正在刷新，当前内容仍可使用</div> : null}{stale ? <div className="stale-warning" role="status"><AlertTriangle size={14} />刷新失败，当前内容可能已过期{onRetry ? <button className="ghost" onClick={onRetry}>重试</button> : null}</div> : null}{children}</>;
}

export function useDialogFocus(open: boolean, panelRef: RefObject<HTMLElement | null>, onClose: () => void) {
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const panel = panelRef.current;
    const focusable = () => Array.from(panel?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || []);
    focusable()[0]?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) { event.preventDefault(); panel?.focus(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      previous?.focus();
    };
  }, [open, panelRef]);
}

export function ConfirmDialog({ open, title, description, confirmLabel = "确认", danger = false, requireText, disabled = false, onClose, onConfirm }: {
  open: boolean; title: string; description: ReactNode; confirmLabel?: string; danger?: boolean; requireText?: string; disabled?: boolean; onClose: () => void; onConfirm: () => void;
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const [typed, setTyped] = useState("");
  useEffect(() => { if (!open) setTyped(""); }, [open]);
  useDialogFocus(open, panelRef, onClose);
  if (!open) return null;
  const allowed = !disabled && (!requireText || typed === requireText);
  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <div ref={panelRef} className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby={titleId}>
        <div className={`confirm-icon ${danger ? "is-danger" : ""}`}><AlertTriangle size={22} /></div>
        <div><h2 id={titleId}>{title}</h2><div className="confirm-description">{description}</div></div>
        {requireText ? <label>输入 <strong>{requireText}</strong> 以确认<input autoFocus value={typed} onChange={(event) => setTyped(event.target.value)} /></label> : null}
        <div className="dialog-actions"><button className="ghost" onClick={onClose}>取消</button><button className={danger ? "danger-button" : ""} disabled={!allowed} onClick={onConfirm}>{confirmLabel}</button></div>
      </div>
    </div>
  );
}

type Toast = { id: number; message: string; tone: "success" | "error" };
const ToastContext = createContext<(message: string, tone?: Toast["tone"]) => void>(() => undefined);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((message: string, tone: Toast["tone"] = "success") => {
    const id = Date.now();
    setToasts((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 4000);
  }, []);
  return <ToastContext.Provider value={push}>{children}<div className="toast-region" aria-live="polite">{toasts.map((toast) => <div className={`toast is-${toast.tone}`} key={toast.id}>{toast.tone === "success" ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}<span>{toast.message}</span><button aria-label="关闭提示" onClick={() => setToasts((items) => items.filter((item) => item.id !== toast.id))}><X size={15} /></button></div>)}</div></ToastContext.Provider>;
}
export function useToast() { return useContext(ToastContext); }
