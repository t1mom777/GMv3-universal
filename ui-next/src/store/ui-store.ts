import { create } from "zustand";

export type AppTab = "setup" | "play" | "advanced";

export type ChatMessage = {
  id: string;
  role: "gm" | "player" | "system" | "error";
  speaker: string;
  text: string;
  ts: number;
};

type UIState = {
  activeTab: AppTab;
  wsStatus: "disconnected" | "connecting" | "connected";
  wsUrl: string;
  messages: ChatMessage[];
  setActiveTab: (tab: AppTab) => void;
  setWsStatus: (status: UIState["wsStatus"]) => void;
  setWsUrl: (url: string) => void;
  addMessage: (msg: ChatMessage) => void;
  clearMessages: () => void;
};

export const useUIStore = create<UIState>((set) => ({
  activeTab: "setup",
  wsStatus: "disconnected",
  wsUrl: "",
  messages: [],
  setActiveTab: (tab) => set({ activeTab: tab }),
  setWsStatus: (wsStatus) => set({ wsStatus }),
  setWsUrl: (wsUrl) => set({ wsUrl }),
  addMessage: (msg) =>
    set((state) => {
      const next = [...state.messages, msg];
      return { messages: next.slice(-300) };
    }),
  clearMessages: () => set({ messages: [] }),
}));
