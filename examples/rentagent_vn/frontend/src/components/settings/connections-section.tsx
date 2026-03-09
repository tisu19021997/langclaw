"use client";

import { useEffect } from "react";
import { ChevronRight } from "lucide-react";
import { useZaloStore } from "@/stores/zalo-store";

interface ConnectionRowProps {
  iconBg: string;
  emoji: string;
  label: string;
  sub: string;
  connected: boolean;
  onClick?: () => void;
}

function ConnectionRow({
  iconBg,
  emoji,
  label,
  sub,
  connected,
  onClick,
}: ConnectionRowProps) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-3 py-3 px-4 text-left"
    >
      {/* Icon */}
      <div
        className="flex-shrink-0 flex items-center justify-center text-xl"
        style={{
          width: 36,
          height: 36,
          borderRadius: "var(--r-sm)",
          background: iconBg,
        }}
      >
        {emoji}
      </div>

      {/* Labels */}
      <div className="flex-1 min-w-0">
        <div
          className="text-[14px] font-semibold"
          style={{ color: "var(--ink)" }}
        >
          {label}
        </div>
        <div className="text-[12px] truncate" style={{ color: "var(--ink-50)" }}>
          {sub}
        </div>
      </div>

      {/* Status chip + chevron */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <span
          className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
          style={{
            background: connected ? "var(--jade-15)" : "var(--ink-08)",
            color: connected ? "var(--jade)" : "var(--ink-30)",
          }}
        >
          {connected ? "Đã kết nối" : "Chưa kết nối"}
        </span>
        <ChevronRight size={14} style={{ color: "var(--ink-30)" }} />
      </div>
    </button>
  );
}

export function ConnectionsSection() {
  const { status, fetchStatus } = useZaloStore();

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const zaloConnected = status?.connected ?? false;

  return (
    <div
      style={{
        background: "var(--ds-white)",
        borderRadius: "var(--r-lg)",
        border: "1px solid var(--ink-08)",
        overflow: "hidden",
      }}
    >
      <ConnectionRow
        iconBg="#e6f4ec"
        emoji="💬"
        label="Zalo"
        sub="Nhắn tin và nhận thông báo"
        connected={zaloConnected}
        onClick={() => {
          // TODO: open ZaloSettingsDialog — wire in Phase 2 or reuse existing component
        }}
      />
      <div style={{ borderTop: "1px solid var(--ink-04)" }} />
      <ConnectionRow
        iconBg="#e8edf8"
        emoji="📘"
        label="Facebook"
        sub="Quét tin từ các nhóm cho thuê"
        connected={false}
        onClick={() => {}}
      />
      <div style={{ borderTop: "1px solid var(--ink-04)" }} />
      <ConnectionRow
        iconBg="#f5ece8"
        emoji="🏠"
        label="BDS.com.vn"
        sub="Nguồn tin chính"
        connected={true}
        onClick={() => {}}
      />
    </div>
  );
}
