export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
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
  if (!response.ok || (typeof data === "object" && data && "error" in data)) {
    const message = typeof data === "object" && data && "error" in data ? String(data.error) : "请求失败";
    throw new ApiError(message, response.status);
  }
  return data as T;
}
