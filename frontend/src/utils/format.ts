export function formatDateTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function asCount(batch: { total_count?: number; total?: number; completed_count?: number; completed?: number; success_count?: number; success?: number; failed_count?: number; failed?: number }) {
  return {
    total: Number(batch.total_count ?? batch.total ?? 0),
    completed: Number(batch.completed_count ?? batch.completed ?? 0),
    success: Number(batch.success_count ?? batch.success ?? 0),
    failed: Number(batch.failed_count ?? batch.failed ?? 0)
  };
}

export function pct(done: number, total: number) {
  if (!total) return 0;
  return Math.round((done / total) * 100);
}
