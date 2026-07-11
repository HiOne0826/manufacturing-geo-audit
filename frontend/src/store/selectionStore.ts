import { create } from "zustand";
import { persist } from "zustand/middleware";

type SelectionState = {
  projectId: number | null;
  setProjectId: (projectId: number | null) => void;
};

export const useSelectionStore = create<SelectionState>()(persist((set) => ({
  projectId: null,
  setProjectId: (projectId) => set({ projectId })
}), { name: "ostrich-geo-selection", partialize: (state) => ({ projectId: state.projectId }) }));
