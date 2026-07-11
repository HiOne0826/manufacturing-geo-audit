import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, useLocation } from "react-router-dom";
import { useSelectionStore } from "../store/selectionStore";
import { AppShell } from "./AppShell";

const server = setupServer(
  http.get("*/api/projects", () => HttpResponse.json({
    projects: [
      { id: 1, client_name: "客户 A", brand_name: "品牌 A", archived_at: null },
      { id: 2, client_name: "客户 B", brand_name: "品牌 B", archived_at: null },
    ],
  })),
  http.get("*/api/tasks/active", () => HttpResponse.json({ batches: [], stale: false })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  useSelectionStore.setState({ projectId: null });
});
afterAll(() => server.close());

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="location">{location.pathname}{location.search}</output>;
}

describe("project context", () => {
  it("restores a deep-linked project and switches URL and store together", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/analysis?project_id=2"]}>
          <AppShell><LocationProbe /></AppShell>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const select = await screen.findByRole("combobox", { name: "切换当前项目" });
    await waitFor(() => expect(select).toHaveValue("2"));
    expect(useSelectionStore.getState().projectId).toBe(2);

    await user.selectOptions(select, "1");
    await waitFor(() => expect(useSelectionStore.getState().projectId).toBe(1));
    expect(screen.getByLabelText("location")).toHaveTextContent("/analysis?project_id=1");
  });

  it.each(["/models", "/settings"])("hides the project topbar on %s", async (path) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <AppShell><div>页面内容</div></AppShell>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(container.querySelector('select[aria-label="切换当前项目"]')).not.toBeInTheDocument();
    expect(container.querySelector('button.task-center-trigger')).not.toBeInTheDocument();
  });
});
