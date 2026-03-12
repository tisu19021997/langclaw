"use client";

import { SearchPreferencesCard } from "./search-preferences-card";
import { SourcesCard } from "./sources-card";
import { ScheduleSection } from "./schedule-section";
import { NotificationsSection } from "./notifications-section";
import { OutreachSection } from "./outreach-section";
import { ChannelsSection } from "./channels-section";
import { AboutSection } from "./about-section";
import { SettingsSectionLabel } from "@/components/shared";

interface SettingsScreenProps {
  campaignId: string;
  campaignPill?: React.ReactNode;
}

export function SettingsScreen({ campaignId, campaignPill }: SettingsScreenProps) {
  return (
    <div
      className="flex flex-col h-full overflow-y-auto"
      style={{ background: "var(--cream)", paddingBottom: 40 }}
    >
      {/* Header */}
      <div className="px-5 pt-4 pb-4">
        {/* Campaign pill */}
        {campaignPill && <div className="mb-2">{campaignPill}</div>}
        <h1
          className="text-[22px] font-extrabold"
          style={{ color: "var(--ink)", letterSpacing: "-0.8px" }}
        >
          Settings
        </h1>
      </div>

      <div className="px-5 space-y-6">
        {/* Section: Search Settings */}
        <div>
          <SettingsSectionLabel>Search</SettingsSectionLabel>
          <div className="space-y-3">
            <SearchPreferencesCard campaignId={campaignId} />
            <SourcesCard campaignId={campaignId} />
          </div>
        </div>

        {/* Section: Schedule */}
        <ScheduleSection campaignId={campaignId} />

        {/* Section: Notifications */}
        <NotificationsSection campaignId={campaignId} />

        {/* Section: Outreach */}
        <OutreachSection campaignId={campaignId} />

        {/* Section: Channels */}
        <ChannelsSection />

        {/* Section: About */}
        <AboutSection />
      </div>
    </div>
  );
}
