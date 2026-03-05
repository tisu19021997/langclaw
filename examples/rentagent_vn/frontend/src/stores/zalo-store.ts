import { create } from "zustand";
import type { ZaloStatus } from "@/types";
import * as api from "@/lib/api";

const ZALO_CREDENTIALS_KEY = "zalo_credentials";

interface ZaloCredentials {
  cookie: string;
  imei: string;
  userAgent: string;
}

function saveCredentials(credentials: ZaloCredentials) {
  try {
    localStorage.setItem(ZALO_CREDENTIALS_KEY, JSON.stringify(credentials));
  } catch {
    // localStorage might be unavailable
  }
}

function loadCredentials(): ZaloCredentials | null {
  try {
    const stored = localStorage.getItem(ZALO_CREDENTIALS_KEY);
    if (stored) {
      return JSON.parse(stored);
    }
  } catch {
    // localStorage might be unavailable or corrupted
  }
  return null;
}

function clearCredentials() {
  try {
    localStorage.removeItem(ZALO_CREDENTIALS_KEY);
  } catch {
    // localStorage might be unavailable
  }
}

interface ZaloState {
  status: ZaloStatus | null;
  connecting: boolean;
  error: string | null;

  fetchStatus: () => Promise<void>;
  connectCookie: (
    cookie: string,
    imei: string,
    userAgent: string
  ) => Promise<void>;
  disconnect: () => Promise<void>;
  clearError: () => void;
}

export const useZaloStore = create<ZaloState>((set, get) => ({
  status: null,
  connecting: false,
  error: null,

  fetchStatus: async () => {
    try {
      const status = await api.getZaloStatus();

      // If backend says not connected but we have stored credentials, auto-reconnect
      if (!status.connected) {
        const credentials = loadCredentials();
        if (credentials && !get().connecting) {
          console.log("Zalo not connected, attempting auto-reconnect...");
          try {
            set({ connecting: true });
            const newStatus = await api.connectZaloCookie(
              credentials.cookie,
              credentials.imei,
              credentials.userAgent
            );
            set({ status: newStatus, connecting: false, error: null });
            return;
          } catch (e) {
            console.error("Auto-reconnect failed:", e);
            // Clear invalid credentials
            clearCredentials();
            set({ connecting: false });
          }
        }
      }

      set({ status, error: null });
    } catch (e) {
      set({
        status: { connected: false, phone_number: null, error: null },
        error: (e as Error).message,
      });
    }
  },

  connectCookie: async (cookie, imei, userAgent) => {
    set({ connecting: true, error: null });
    try {
      const status = await api.connectZaloCookie(cookie, imei, userAgent);
      // Save credentials on successful connection
      saveCredentials({ cookie, imei, userAgent });
      set({ status, connecting: false });
    } catch (e) {
      set({ connecting: false, error: (e as Error).message });
      throw e;
    }
  },

  disconnect: async () => {
    set({ connecting: true, error: null });
    try {
      const status = await api.disconnectZalo();
      // Clear stored credentials on disconnect
      clearCredentials();
      set({ status, connecting: false });
    } catch (e) {
      set({ connecting: false, error: (e as Error).message });
    }
  },

  clearError: () => set({ error: null }),
}));
