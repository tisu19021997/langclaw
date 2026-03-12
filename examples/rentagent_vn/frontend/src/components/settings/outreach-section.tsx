"use client";

import { useCampaignStore } from "@/stores/campaign-store";
import {
  SettingsToggle,
  SettingsRow,
  SettingsGroup,
  SettingsSectionLabel,
} from "@/components/shared";

interface OutreachSectionProps {
  campaignId: string;
}

export function OutreachSection({ campaignId }: OutreachSectionProps) {
  const campaign = useCampaignStore((s) => s.campaign);
  const setOutreachAutoSend = useCampaignStore((s) => s.setOutreachAutoSend);

  const autoSendOn = campaign?.preferences?.outreach_auto_send ?? false;

  const handleAutoSendToggle = async (val: boolean) => {
    await setOutreachAutoSend(campaignId, val);
  };

  return (
    <div>
      <SettingsSectionLabel>Outreach</SettingsSectionLabel>
      <SettingsGroup>
        <SettingsRow
          label="Auto-send messages"
          sub="Send immediately when you tap Contact now"
          toggle={
            <SettingsToggle
              on={autoSendOn}
              onChange={handleAutoSendToggle}
            />
          }
        />
      </SettingsGroup>
    </div>
  );
}
