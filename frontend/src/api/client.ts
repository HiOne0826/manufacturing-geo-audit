export class ApiError extends Error {
  status: number;
  code?: string;
  details?: Record<string, unknown>;

  constructor(message: string, status: number, details?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.details = details;
    this.code = typeof details?.code === "string" ? details.code : undefined;
  }
}

export function apiPath(path: string): string {
  if (!path.startsWith("/")) return path;
  const base = import.meta.env.BASE_URL === "/" ? "" : import.meta.env.BASE_URL.replace(/\/$/, "");
  return `${base}${path}`;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (!headers.has("Content-Type") && options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(apiPath(path), { ...options, headers, credentials: "same-origin" });
  const contentType = response.headers.get("Content-Type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  const apiError = typeof data === "object" && data && "error" in data && Boolean(data.error);
  if (!response.ok || apiError) {
    const message = apiError ? String((data as { error: unknown }).error) : "请求失败";
    throw new ApiError(message, response.status, typeof data === "object" && data ? data as Record<string, unknown> : undefined);
  }
  return data as T;
}
