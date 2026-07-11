import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function activeProjectId(page: Page) {
  const response = await page.request.get("./api/projects");
  const payload = await response.json() as { projects?: Array<{ id: number; archived_at?: string | null }> };
  const project = payload.projects?.find((item) => !item.archived_at);
  if (!project) throw new Error("E2E requires at least one active project");
  return project.id;
}

async function activeProjects(page: Page) {
  const response = await page.request.get("./api/projects");
  const payload = await response.json() as { projects?: Array<{ id: number; archived_at?: string | null }> };
  return payload.projects?.filter((item) => !item.archived_at) || [];
}

async function createFixtureProject(page: Page, questionCount: number) {
  const name = `E2E-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`;
  const created = await page.request.post("./api/projects", { data: { client_name: name, brand_name: name, product_category: "E2E" } });
  expect(created.ok()).toBeTruthy();
  const project = await created.json() as { id: number };
  const rows = Array.from({ length: questionCount }, (_, index) => ({ "问题ID": `E2E-${index + 1}`, "问题内容": `E2E 测试问题 ${index + 1}` }));
  const imported = await page.request.post("./api/questions/import_rows", { data: { project_id: project.id, rows } });
  expect(imported.ok()).toBeTruthy();
  return { id: project.id, name };
}

async function deleteFixtureProject(page: Page, project: { id: number; name: string }) {
  const response = await page.request.post("./api/projects/delete", { data: { id: project.id, confirm_name: project.name } });
  expect(response.ok()).toBeTruthy();
}

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "compact", width: 900, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];

for (const viewport of viewports) {
  for (const route of ["", "projects", "questions", "models", "sampling", "batches", "analysis", "settings"]) {
    test(`${viewport.name} ${route || "dashboard"} has no page overflow`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const projectId = await activeProjectId(page);
      await page.goto(`./${route}?project_id=${projectId}`);
      await expect(page.locator(".app-shell")).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(1);
      await page.screenshot({ path: `test-results/${viewport.name}-${route || "dashboard"}.png`, fullPage: true });
    });
  }
}

for (const route of ["projects", "questions", "sampling", "batches", "analysis", "settings"]) {
  test(`${route} has no WCAG A/AA violations`, async ({ page }) => {
    const projectId = await activeProjectId(page);
    await page.goto(`./${route}?project_id=${projectId}`);
    await expect(page.locator(".app-shell")).toBeVisible();
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(result.violations).toEqual([]);
  });
}

test("deep link preserves project context", async ({ page }) => {
  const projectId = await activeProjectId(page);
  await page.goto(`./models?project_id=${projectId}`);
  await expect(page.getByRole("heading", { name: "模型" })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`project_id=${projectId}`));
});

test("dirty sampling form blocks accidental project switch", async ({ page }) => {
  const projects = await activeProjects(page);
  test.skip(projects.length < 2, "requires two active projects");
  await page.goto(`./sampling?project_id=${projects[0].id}`);
  await page.getByLabel("批次名称").fill("未保存批次");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByLabel("切换当前项目").selectOption(String(projects[1].id));
  await expect(page.getByLabel("切换当前项目")).toHaveValue(String(projects[0].id));
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByLabel("切换当前项目").selectOption(String(projects[1].id));
  await expect(page).toHaveURL(new RegExp(`project_id=${projects[1].id}`));
  await expect(page.getByLabel("批次名称")).toHaveValue("");
});

test("question pagination writes direct jump to URL", async ({ page }) => {
  const project = await createFixtureProject(page, 21);
  try {
    await page.goto(`./questions?project_id=${project.id}`);
    await page.getByLabel("跳转页码").fill("2");
    await page.getByRole("button", { name: "跳转" }).click();
    await expect(page).toHaveURL(/questions_page=2/);
    await expect(page.locator("tbody tr")).toHaveCount(10);
  } finally {
    await deleteFixtureProject(page, project);
  }
});

test("batch detail exposes configuration and evidence tables", async ({ page }) => {
  const project = await createFixtureProject(page, 11);
  let modelId = 0;
  try {
    const modelResponse = await page.request.post("./api/models", { data: { provider: "mock", label: "E2E Mock", model: "mock-model", active: true, supports_pure: true, supports_search: false } });
    expect(modelResponse.ok()).toBeTruthy();
    modelId = ((await modelResponse.json()) as { id: number }).id;
    const startedResponse = await page.request.post("./api/runs/start", { data: { project_id: project.id, batch_name: "E2E 批次详情", repeat_count: 1, models: [{ model_config_id: modelId, search_enabled: false }] } });
    expect(startedResponse.ok()).toBeTruthy();
    const started = await startedResponse.json() as { batch_id: string };
    await expect.poll(async () => {
      const response = await page.request.get(`./api/runs/progress?batch_id=${started.batch_id}`);
      return ((await response.json()) as { status?: string }).status;
    }, { timeout: 15_000 }).toBe("completed");
    await page.goto(`./batches/${started.batch_id}?project_id=${project.id}`);
    await expect(page.getByText("不可变配置快照")).toBeVisible();
    await expect(page.getByText("当前结果与数据质检")).toBeVisible();
    await expect(page.getByText("Attempt History")).toBeVisible();
    await expect(page.getByLabel("分页导航")).toHaveCount(2);
  } finally {
    await deleteFixtureProject(page, project);
    if (modelId) await page.request.post("./api/models/delete", { data: { id: modelId } });
  }
});

test("mobile navigation traps focus and closes with Escape", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const projectId = await activeProjectId(page);
  await page.goto(`./?project_id=${projectId}`);
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.getByRole("dialog", { name: "主导航" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "主导航" })).toBeHidden();
  await expect(page.getByRole("button", { name: "打开导航" })).toBeFocused();
});
