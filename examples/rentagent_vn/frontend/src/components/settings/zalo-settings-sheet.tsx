"use client";

import { useState, useEffect } from "react";
import { Wifi, WifiOff } from "lucide-react";
import { BottomSheet } from "@/components/shared";
import { useZaloStore } from "@/stores/zalo-store";

interface ZaloSettingsSheetProps {
  open: boolean;
  onClose: () => void;
}

export function ZaloSettingsSheet({ open, onClose }: ZaloSettingsSheetProps) {
  const { status, connecting, error, fetchStatus, connectCookie, disconnect, clearError } =
    useZaloStore();

  const [cookie, setCookie] = useState("");
  const [imei, setImei] = useState("");
  const [userAgent, setUserAgent] = useState("");

  useEffect(() => {
    if (open) {
      fetchStatus();
      if (typeof navigator !== "undefined") {
        setUserAgent(navigator.userAgent);
      }
      clearError();
    }
  }, [open, fetchStatus, clearError]);

  const handleConnect = async () => {
    if (!cookie.trim() || !imei.trim() || !userAgent.trim()) return;
    try {
      await connectCookie(cookie, imei, userAgent);
      setCookie("");
      setImei("");
    } catch {
      // Error handled in store
    }
  };

  const handleDisconnect = async () => {
    await disconnect();
  };

  const isConnected = status?.connected ?? false;
  const canConnect = cookie.trim() && imei.trim() && userAgent.trim();

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title="Connect Zalo"
      subtitle="Contact landlords directly from the app"
      footer={
        isConnected ? (
          <button
            onClick={handleDisconnect}
            disabled={connecting}
            className="w-full h-[52px] text-[15px] font-semibold transition-colors"
            style={{
              background: "transparent",
              color: "var(--ink)",
              borderRadius: "var(--r-lg)",
              border: "1px solid var(--ink-15)",
            }}
          >
            {connecting ? "Disconnecting..." : "Disconnect"}
          </button>
        ) : (
          <button
            onClick={handleConnect}
            disabled={!canConnect || connecting}
            className="w-full h-[52px] text-[15px] font-semibold transition-colors"
            style={{
              background: canConnect && !connecting ? "var(--terra)" : "var(--ink-15)",
              color: canConnect && !connecting ? "white" : "var(--ink-30)",
              borderRadius: "var(--r-lg)",
            }}
          >
            {connecting ? "Connecting..." : "Connect"}
          </button>
        )
      }
    >
      <div className="pb-4 space-y-4">
        {/* Connection Status */}
        <div
          className="flex items-center gap-3 p-4"
          style={{
            background: "var(--ds-white)",
            borderRadius: "var(--r-lg)",
            border: isConnected ? "1px solid var(--jade)" : "1px solid var(--ink-08)",
          }}
        >
          {isConnected ? (
            <Wifi size={20} style={{ color: "var(--jade)" }} />
          ) : (
            <WifiOff size={20} style={{ color: "var(--ink-30)" }} />
          )}
          <div className="flex-1">
            <p
              className="text-[14px] font-semibold"
              style={{ color: "var(--ink)" }}
            >
              {isConnected ? "Connected" : "Not connected"}
            </p>
            {isConnected && status?.phone_number && (
              <p className="text-[12px]" style={{ color: "var(--ink-50)" }}>
                Phone: {status.phone_number}
              </p>
            )}
          </div>
          {isConnected && (
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: "var(--jade)" }}
            />
          )}
        </div>

        {/* Error */}
        {error && (
          <div
            className="p-3 text-[13px]"
            style={{
              background: "rgba(192, 0, 48, 0.08)",
              borderRadius: "var(--r-sm)",
              color: "#C03",
            }}
          >
            {error}
          </div>
        )}

        {isConnected ? (
          <p className="text-[13px]" style={{ color: "var(--ink-50)" }}>
            Your Zalo account is connected. You can send messages to landlords
            directly from listing details.
          </p>
        ) : (
          <>
            {/* Instructions */}
            <div
              className="p-4"
              style={{
                background: "var(--ds-white)",
                borderRadius: "var(--r-lg)",
                border: "1px solid var(--ink-08)",
              }}
            >
              <p
                className="text-[12px] font-semibold mb-3"
                style={{ color: "var(--ink)" }}
              >
                How to get login info:
              </p>
              <ol
                className="text-[12px] space-y-2 list-decimal list-inside"
                style={{ color: "var(--ink-50)" }}
              >
                <li>Open chat.zalo.me in Chrome</li>
                <li>Press F12 to open DevTools</li>
                <li>Select Application → Cookies</li>
                <li>Copy all cookies as JSON</li>
                <li>IMEI can be found in Network tab when sending a message</li>
              </ol>
            </div>

            {/* Input Fields */}
            <div className="space-y-3">
              <div>
                <label
                  className="block text-[11px] font-semibold uppercase tracking-wide mb-2"
                  style={{ color: "var(--ink-30)", letterSpacing: "0.5px" }}
                >
                  Zalo Cookie
                </label>
                <textarea
                  value={cookie}
                  onChange={(e) => setCookie(e.target.value)}
                  placeholder='{"zpw_sek": "...", "zpw_uid": "...", ...}'
                  className="w-full px-4 py-3 text-[13px] font-mono outline-none resize-none"
                  style={{
                    background: "var(--ds-white)",
                    border: "1px solid var(--ink-15)",
                    borderRadius: "var(--r-sm)",
                    color: "var(--ink)",
                    height: 100,
                  }}
                />
              </div>

              <div>
                <label
                  className="block text-[11px] font-semibold uppercase tracking-wide mb-2"
                  style={{ color: "var(--ink-30)", letterSpacing: "0.5px" }}
                >
                  IMEI
                </label>
                <input
                  value={imei}
                  onChange={(e) => setImei(e.target.value)}
                  placeholder="Enter IMEI from DevTools"
                  className="w-full px-4 py-3 text-[13px] font-mono outline-none"
                  style={{
                    background: "var(--ds-white)",
                    border: "1px solid var(--ink-15)",
                    borderRadius: "var(--r-sm)",
                    color: "var(--ink)",
                  }}
                />
              </div>

              <div>
                <label
                  className="block text-[11px] font-semibold uppercase tracking-wide mb-2"
                  style={{ color: "var(--ink-30)", letterSpacing: "0.5px" }}
                >
                  User Agent
                </label>
                <input
                  value={userAgent}
                  onChange={(e) => setUserAgent(e.target.value)}
                  placeholder="Mozilla/5.0..."
                  className="w-full px-4 py-3 text-[13px] font-mono outline-none"
                  style={{
                    background: "var(--ds-white)",
                    border: "1px solid var(--ink-15)",
                    borderRadius: "var(--r-sm)",
                    color: "var(--ink)",
                  }}
                />
              </div>
            </div>
          </>
        )}
      </div>
    </BottomSheet>
  );
}
