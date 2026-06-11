import { create } from "zustand";

type SelectionState = {
  projectId: number | null;
  setProjectId: (projectId: number | null) => void;
};

export const useSelectionStore = create<SelectionState>((set) => ({
  projectId: null,
  setProjectId: (projectId) => set({ projectId })
}));
