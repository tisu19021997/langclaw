"use client";

import { useCampaignStore } from "@/stores/campaign-store";
import {
  SettingsToggle,
  SettingsRow,
  SettingsGroup,
  SettingsSectionLabel,
} from "@/components/shared";

interface ScheduleSectionProps {
  campaignId: string;
}

export function ScheduleSection({ campaignId }: ScheduleSectionProps) {
  const campaign = useCampaignStore((s) => s.campaign);
  const updateCampaign = useCampaignStore((s) => s.updateCampaign);

  const autoScanOn = campaign?.scan_frequency !== "manual";

  const handleAutoScanToggle = async (val: boolean) => {
    await updateCampaign(campaignId, {
      scan_frequency: val ? "auto" : "manual",
    });
  };

  return (
    <div>
      <SettingsSectionLabel>Schedule</SettingsSectionLabel>
      <SettingsGroup>
        <SettingsRow
          label="Auto scan"
          sub="Every 2 hours · 7:00 AM – 10:00 PM"
          toggle={
            <SettingsToggle
              on={autoScanOn}
              onChange={handleAutoScanToggle}
            />
          }
        />
      </SettingsGroup>
    </div>
  );
}
