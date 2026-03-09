"use client";

import { useState } from "react";
import { useCampaignStore } from "@/stores/campaign-store";

interface ToggleProps {
  on: boolean;
  onChange?: (val: boolean) => void;
  disabled?: boolean;
}

function Toggle({ on, onChange, disabled = false }: ToggleProps) {
  return (
    <button
      onClick={() => !disabled && onChange?.(!on)}
      className="flex-shrink-0 relative"
      style={{
        width: 44,
        height: 26,
        borderRadius: "var(--r-full)",
        background: on ? "var(--terra)" : "var(--ink-15)",
        transition: "background 0.2s",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <div
        className="absolute top-[3px] rounded-full bg-white transition-transform"
        style={{
          width: 20,
          height: 20,
          left: 3,
          transform: on ? "translateX(18px)" : "translateX(0)",
          boxShadow: "0 1px 4px rgba(0,0,0,.2)",
          transition: "transform 0.2s",
        }}
      />
    </button>
  );
}

interface SettingsRowProps {
  label: string;
  sub: string;
  toggle: React.ReactNode;
}

function SettingsRow({ label, sub, toggle }: SettingsRowProps) {
  return (
    <div className="flex items-center gap-3 py-3 px-4">
      <div className="flex-1 min-w-0">
        <div className="text-[14px] font-semibold" style={{ color: "var(--ink)" }}>
          {label}
        </div>
        <div className="text-[12px]" style={{ color: "var(--ink-50)" }}>
          {sub}
        </div>
      </div>
      {toggle}
    </div>
  );
}

interface ScheduleSectionProps {
  campaignId: string;
}

export function ScheduleSection({ campaignId }: ScheduleSectionProps) {
  const { campaign, updateCampaign } = useCampaignStore();
  const [notifOn, setNotifOn] = useState(true);
  const [researchNotifOn, setResearchNotifOn] = useState(true);

  const autoScanOn = campaign?.scan_frequency !== "manual";

  const handleAutoScanToggle = async (val: boolean) => {
    await updateCampaign(campaignId, {
      scan_frequency: val ? "auto" : "manual",
    });
  };

  return (
    <div className="space-y-4">
      {/* Lịch hoạt động */}
      <div>
        <p
          className="text-[11px] font-semibold uppercase mb-2 px-1"
          style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
        >
          Lịch hoạt động
        </p>
        <div
          style={{
            background: "var(--ds-white)",
            borderRadius: "var(--r-lg)",
            border: "1px solid var(--ink-08)",
            overflow: "hidden",
          }}
        >
          <SettingsRow
            label="Quét tự động"
            sub="Mỗi 2 giờ · 7:00 – 22:00"
            toggle={
              <Toggle
                on={autoScanOn}
                onChange={handleAutoScanToggle}
              />
            }
          />
          <div style={{ borderTop: "1px solid var(--ink-04)" }} />
          <SettingsRow
            label="Thông báo tin mới"
            sub="Ngay khi có tin phù hợp"
            toggle={
              <Toggle
                on={notifOn}
                onChange={setNotifOn}
              />
            }
          />
        </div>
      </div>

      {/* Phân tích khu vực */}
      <div>
        <p
          className="text-[11px] font-semibold uppercase mb-2 px-1"
          style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
        >
          Phân tích khu vực
        </p>
        <div
          style={{
            background: "var(--ds-white)",
            borderRadius: "var(--r-lg)",
            border: "1px solid var(--ink-08)",
            overflow: "hidden",
          }}
        >
          <SettingsRow
            label="Khảo sát tự động"
            sub="Khi bạn nhấn 'Xem thêm'"
            toggle={
              <Toggle
                on={true}
                disabled={true}
              />
            }
          />
          <div style={{ borderTop: "1px solid var(--ink-04)" }} />
          <SettingsRow
            label="Thông báo kết quả khảo sát"
            sub="Khi phân tích khu vực xong"
            toggle={
              <Toggle
                on={researchNotifOn}
                onChange={setResearchNotifOn}
              />
            }
          />
        </div>
      </div>
    </div>
  );
}
