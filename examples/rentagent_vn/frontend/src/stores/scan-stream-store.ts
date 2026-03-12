import { create } from "zustand";
import type {
  ScanSSEEvent,
  ScanProgressStep,
  ScanStreamState,
  ScanStreamStatus,
} from "@/types";

interface ScanStreamActions {
  startStream: (scanId: string) => void;
  handleEvent: (event: ScanSSEEvent) => void;
  reset: () => void;
  setActiveUrl: (url: string) => void;
  setStatus: (status: ScanStreamStatus) => void;
}

const initialState: ScanStreamState = {
  scanId: null,
  status: "idle",
  steps: [],
  streamingUrls: {},
  activeUrl: null,
  totalUrls: 0,
  completedUrls: 0,
  completedSourceUrls: new Set<string>(),
  listingsFound: 0,
  startedAt: null,
};

export const useScanStreamStore = create<ScanStreamState & ScanStreamActions>(
  (set, get) => ({
    ...initialState,

    startStream: (scanId) =>
      set({
        ...initialState,
        scanId,
        status: "connecting",
      }),

    handleEvent: (event) => {
      const state = get();

      switch (event.type) {
        case "started":
          set({
            status: "streaming",
            totalUrls: event.total_urls ?? 0,
            startedAt: event.timestamp,
          });
          break;

        case "progress": {
          const prevSteps = state.steps.map((s) =>
            s.url === event.url && s.status === "running"
              ? {
                  ...s,
                  status: "done" as const,
                  duration: event.timestamp - s.timestamp,
                }
              : s
          );
          const newStep: ScanProgressStep = {
            id: `${event.run_id ?? "unknown"}-${Date.now()}`,
            url: event.url ?? "",
            purpose: event.purpose ?? "",
            timestamp: event.timestamp,
            status: "running",
          };
          set({ steps: [...prevSteps, newStep] });
          break;
        }

        case "streaming_url":
          if (event.url && event.streaming_url) {
            set((s) => ({
              streamingUrls: {
                ...s.streamingUrls,
                [event.url!]: event.streaming_url!,
              },
              activeUrl: s.activeUrl ?? event.url!,
            }));
          }
          break;

        case "url_complete":
          console.log("[scan-stream] url_complete event received:", event.url);
          if (event.url) {
            set((s) => {
              const newSet = new Set([...s.completedSourceUrls, event.url!]);
              console.log("[scan-stream] completedSourceUrls updated:", [...newSet]);
              return { completedSourceUrls: newSet };
            });
          }
          break;

        case "error":
          // Per-URL errors don't set global error status
          break;

        case "complete":
          set({
            status: "complete",
            listingsFound: event.listings_found ?? 0,
            steps: state.steps.map((s) =>
              s.status === "running" ? { ...s, status: "done" as const } : s
            ),
          });
          break;

        case "done":
          // SSE stream closed - no state change needed
          break;
      }
    },

    reset: () => set(initialState),

    setActiveUrl: (url) => set({ activeUrl: url }),

    setStatus: (status) => set({ status }),
  })
);
