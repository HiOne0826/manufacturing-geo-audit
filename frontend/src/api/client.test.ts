import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { ApiError, api } from "./client";

const server = setupServer(
  http.get("*/api/ok", () => HttpResponse.json({ ok: true })),
  http.get("*/api/stale", () => HttpResponse.json({ error: "数据源暂不可用" }, { status: 503 })),
  http.post("*/api/conflict", () => HttpResponse.json({ error: "已有活动批次", code: "ACTIVE_BATCH_EXISTS", batch_id: "batch-1" }, { status: 409 })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("api client", () => {
  it("returns JSON responses", async () => {
    await expect(api<{ ok: boolean }>("/api/ok")).resolves.toEqual({ ok: true });
  });

  it("preserves API error status and message", async () => {
    const error = await api("/api/stale").catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ message: "数据源暂不可用", status: 503 });
  });

  it("preserves structured conflict details for recovery actions", async () => {
    const error = await api("/api/conflict", { method: "POST" }).catch((caught) => caught);
    expect(error).toMatchObject({ status: 409, code: "ACTIVE_BATCH_EXISTS", details: { batch_id: "batch-1" } });
  });
});
