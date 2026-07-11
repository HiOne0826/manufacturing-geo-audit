import { describe, expect, it } from "vitest";
import { analyzeQuestionImport } from "./WorkspacePages";

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
