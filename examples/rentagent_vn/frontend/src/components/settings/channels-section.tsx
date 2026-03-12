"use client";

import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useZaloStore } from "@/stores/zalo-store";
import {
  SettingsGroup,
  SettingsSectionLabel,
} from "@/components/shared";
import { ZaloSettingsSheet } from "./zalo-settings-sheet";

interface ChannelRowProps {
  iconBg: string;
  emoji: string;
  label: string;
  sub: string;
  connected: boolean;
  onClick?: () => void;
}

function ChannelRow({
  iconBg,
  emoji,
  label,
  sub,
  connected,
  onClick,
}: ChannelRowProps) {
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
          {connected ? "Connected" : "Not connected"}
        </span>
        <ChevronRight size={14} style={{ color: "var(--ink-30)" }} />
      </div>
    </button>
  );
}

export function ChannelsSection() {
  const [zaloSheetOpen, setZaloSheetOpen] = useState(false);
  const { status, fetchStatus } = useZaloStore();

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const zaloConnected = status?.connected ?? false;

  return (
    <div>
      <SettingsSectionLabel>Channels</SettingsSectionLabel>
      <SettingsGroup>
        <ChannelRow
          iconBg="#e6f4ec"
          emoji="💬"
          label="Zalo"
          sub="Messaging and notifications"
          connected={zaloConnected}
          onClick={() => setZaloSheetOpen(true)}
        />
      </SettingsGroup>

      <ZaloSettingsSheet
        open={zaloSheetOpen}
        onClose={() => setZaloSheetOpen(false)}
      />
    </div>
  );
}
