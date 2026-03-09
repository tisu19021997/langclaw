"use client";

import { SearchQueryCard } from "./search-query-card";
import { ConnectionsSection } from "./connections-section";
import { ScheduleSection } from "./schedule-section";

interface SettingsScreenProps {
  campaignId: string;
}

export function SettingsScreen({ campaignId }: SettingsScreenProps) {
  return (
    <div
      className="flex flex-col h-full overflow-y-auto"
      style={{ background: "var(--cream)", paddingBottom: 40 }}
    >
      {/* Header */}
      <div className="px-5 pt-5 pb-4">
        <h1
          className="text-[22px] font-extrabold"
          style={{ color: "var(--ink)", letterSpacing: "-0.8px" }}
        >
          Cài đặt
        </h1>
      </div>

      <div className="px-5 space-y-6">
        {/* Section: Tìm kiếm hiện tại */}
        <div>
          <p
            className="text-[11px] font-semibold uppercase mb-2 px-1"
            style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
          >
            Tìm kiếm hiện tại
          </p>
          <SearchQueryCard />
        </div>

        {/* Section: Kết nối */}
        <div>
          <p
            className="text-[11px] font-semibold uppercase mb-2 px-1"
            style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
          >
            Kết nối
          </p>
          <ConnectionsSection />
        </div>

        {/* Schedules + research toggles */}
        <ScheduleSection campaignId={campaignId} />
      </div>
    </div>
  );
}
