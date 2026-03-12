"use client";

import { ChevronRight, MessageSquare } from "lucide-react";
import {
  SettingsGroup,
  SettingsSectionLabel,
  SettingsDivider,
} from "@/components/shared";

export function AboutSection() {
  const handleFeedback = () => {
    window.open("mailto:feedback@rentagent.vn?subject=Góp ý về ứng dụng", "_blank");
  };

  return (
    <div>
      <SettingsSectionLabel>About</SettingsSectionLabel>
      <SettingsGroup>
        <div className="flex items-center gap-3 py-3 px-4">
          <div className="flex-1 min-w-0">
            <div
              className="text-[14px] font-semibold"
              style={{ color: "var(--ink)" }}
            >
              Version
            </div>
            <div className="text-[12px]" style={{ color: "var(--ink-50)" }}>
              v1.0.0
            </div>
          </div>
        </div>
        <SettingsDivider />
        <button
          onClick={handleFeedback}
          className="w-full flex items-center gap-3 py-3 px-4 text-left"
        >
          <div className="flex-1 min-w-0">
            <div
              className="text-[14px] font-semibold"
              style={{ color: "var(--ink)" }}
            >
              Feedback
            </div>
            <div className="text-[12px]" style={{ color: "var(--ink-50)" }}>
              Send us your thoughts
            </div>
          </div>
          <ChevronRight size={14} style={{ color: "var(--ink-30)" }} />
        </button>
      </SettingsGroup>
    </div>
  );
}
