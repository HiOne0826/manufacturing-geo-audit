import { beforeEach, describe, expect, it } from "vitest";
import { useSelectionStore } from "./selectionStore";

describe("project selection", () => {
  beforeEach(() => useSelectionStore.setState({ projectId: null }));

  it("can atomically clear the active project on context reset", () => {
    useSelectionStore.getState().setProjectId(13);
    expect(useSelectionStore.getState().projectId).toBe(13);
    useSelectionStore.getState().setProjectId(null);
    expect(useSelectionStore.getState().projectId).toBeNull();
  });
});
