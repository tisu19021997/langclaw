"use client";

import { useCampaignStore } from "@/stores/campaign-store";
import {
  SettingsToggle,
  SettingsRow,
  SettingsGroup,
  SettingsDivider,
  SettingsSectionLabel,
} from "@/components/shared";

interface NotificationsSectionProps {
  campaignId: string;
}

export function NotificationsSection({ campaignId }: NotificationsSectionProps) {
  const campaign = useCampaignStore((s) => s.campaign);
  const updateCampaign = useCampaignStore((s) => s.updateCampaign);

  const notifSettings = campaign?.preferences?.notification_settings ?? {
    new_listings: true,
    research_done: true,
    price_drop: true,
    outreach_reminder: false,
  };

  const handleToggle = async (key: keyof typeof notifSettings, value: boolean) => {
    await updateCampaign(campaignId, {
      preferences: {
        ...campaign?.preferences,
        notification_settings: {
          ...notifSettings,
          [key]: value,
        },
      },
    });
  };

  return (
    <div>
      <SettingsSectionLabel>Notifications</SettingsSectionLabel>
      <SettingsGroup>
        <SettingsRow
          label="New listings"
          sub="When matching listings are found"
          toggle={
            <SettingsToggle
              on={notifSettings.new_listings}
              onChange={(val) => handleToggle("new_listings", val)}
            />
          }
        />
        <SettingsDivider />
        <SettingsRow
          label="Research complete"
          sub="When area analysis is done"
          toggle={
            <SettingsToggle
              on={notifSettings.research_done}
              onChange={(val) => handleToggle("research_done", val)}
            />
          }
        />
        <SettingsDivider />
        <SettingsRow
          label="Price drop"
          sub="When a viewed listing price drops"
          toggle={
            <SettingsToggle
              on={notifSettings.price_drop}
              onChange={(val) => handleToggle("price_drop", val)}
            />
          }
        />
        <SettingsDivider />
        <SettingsRow
          label="Outreach reminder"
          sub="When awaiting reply for > 24h"
          toggle={
            <SettingsToggle
              on={notifSettings.outreach_reminder}
              onChange={(val) => handleToggle("outreach_reminder", val)}
            />
          }
        />
      </SettingsGroup>
    </div>
  );
}
