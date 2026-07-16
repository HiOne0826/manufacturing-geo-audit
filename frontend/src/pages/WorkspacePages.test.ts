import { describe, expect, it } from "vitest";
import { analyzeQuestionImport, citationUrls, splitModelsForManagement, splitModelsForSampling } from "./WorkspacePages";

describe("question paste import", () => {
  it("recognizes Windows, Unix and classic macOS line endings", () => {
    const result = analyzeQuestionImport("问题一\r\n问题二\n问题三\r问题四", new Set());
    expect(result.validRows.map((row) => row["问题内容"])).toEqual(["问题一", "问题二", "问题三", "问题四"]);
  });

  it("skips consecutive blank lines and duplicate questions while preserving preview counts", () => {
    const result = analyzeQuestionImport("问题一\n\n\r\n问题一\r已有问题", new Set(["已有问题"]));
    expect(result.valid).toBe(1);
    expect(result.empty).toBe(2);
    expect(result.duplicate).toBe(2);
    expect(result.invalid).toBe(0);
  });

  it("keeps structured CSV header validation", () => {
    const result = analyzeQuestionImport("编号,标题\r\n1,问题一", new Set());
    expect(result.valid).toBe(0);
    expect(result.invalid).toBe(1);
    expect(result.reasons).toContain("缺少“问题内容”列");
  });
});

describe("model management grouping", () => {
  it("collects GPT, Gemini, DeepSeek official search and MiniMax in the collapsed archive section", () => {
    const groups = splitModelsForManagement([
      { id: 1, provider: "openai", label: "GPT", model: "gpt-5.5" },
      { id: 2, provider: "gemini", label: "Gemini", model: "gemini-2.5-flash" },
      { id: 3, provider: "deepseek_web", label: "DeepSeek 官网联网搜索", model: "DeepSeek Web" },
      { id: 4, provider: "minimax", label: "MiniMax", model: "MiniMax-M1" },
      { id: 5, provider: "qwen", label: "通义千问", model: "qwen3.5-plus" }
    ]);

    expect(groups.archived.map((model) => model.id)).toEqual([1, 2, 3, 4]);
    expect(groups.current.map((model) => model.id)).toEqual([5]);
  });

  it("keeps OpenRouter GPT and Gemini in the main model area", () => {
    const groups = splitModelsForManagement([
      { id: 1, provider: "openrouter_gpt", label: "OpenRouter-GPT", model: "openai/gpt-5.2" },
      { id: 2, provider: "openrouter_gemini", label: "OpenRouter-Gemini", model: "google/gemini-2.5-flash" }
    ]);

    expect(groups.archived).toEqual([]);
    expect(groups.current.map((model) => model.id)).toEqual([1, 2]);
  });

  it("archives manually named GPT and Gemini configurations as well", () => {
    const groups = splitModelsForManagement([
      { id: 1, provider: "custom", label: "企业 GPT", model: "chat-model" },
      { id: 2, provider: "custom", label: "Google 模型", model: "gemini-custom" }
    ]);

    expect(groups.current).toEqual([]);
    expect(groups.archived).toHaveLength(2);
  });
});

describe("sampling model grouping", () => {
  it("archives direct GPT, Gemini and DeepSeek official search while keeping current sampling sources visible", () => {
    const groups = splitModelsForSampling([
      { id: 1, provider: "openai", label: "GPT", model: "gpt-5.5" },
      { id: 2, provider: "gemini", label: "Gemini", model: "gemini-2.5-flash" },
      { id: 3, provider: "deepseek_web", label: "DeepSeek 官网联网搜索", model: "DeepSeek Web" },
      { id: 4, provider: "minimax", label: "MiniMax", model: "minimax-m2.7" },
      { id: 5, provider: "openrouter_gpt", label: "OpenRouter-GPT", model: "openai/gpt-5.2" },
      { id: 6, provider: "openrouter_gemini", label: "OpenRouter-Gemini", model: "google/gemini-2.5-flash" }
    ]);

    expect(groups.archived.map((model) => model.id)).toEqual([1, 2, 3]);
    expect(groups.current.map((model) => model.id)).toEqual([4, 5, 6]);
  });
});

describe("citation source compatibility", () => {
  it("renders the same citations from SQLite JSON text and PostgreSQL JSON arrays", () => {
    const citations = [
      { url: "https://example.com/a" },
      { link: "https://example.com/b" },
      { uri: "https://example.com/a" }
    ];

    expect(citationUrls(JSON.stringify(citations))).toBe("https://example.com/a; https://example.com/b");
    expect(citationUrls(citations)).toBe("https://example.com/a; https://example.com/b");
  });

  it("ignores malformed or unsupported citation payloads", () => {
    expect(citationUrls("not-json")).toBe("");
    expect(citationUrls(JSON.stringify({ url: "https://example.com" }))).toBe("");
    expect(citationUrls()).toBe("");
  });

  it("rejects unsafe citation protocols from provider responses", () => {
    expect(citationUrls([
      { url: "javascript:alert(1)" },
      { url: "data:text/html,unsafe" },
      { url: "https://example.com/safe" }
    ])).toBe("https://example.com/safe");
  });
});
