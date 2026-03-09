"use client";

import { useEffect, useState, useRef } from "react";
import { SetupWizard } from "@/components/setup/setup-wizard";
import { App } from "@/components/app/app";
import { useCampaignStore } from "@/stores/campaign-store";

export default function Home() {
  const { campaigns, fetchCampaigns } = useCampaignStore();
  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const fetchCampaignsRef = useRef(fetchCampaigns);
  fetchCampaignsRef.current = fetchCampaigns;

  useEffect(() => {
    fetchCampaignsRef.current().then(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!ready) return;
    if (activeCampaignId) return;

    const active = campaigns.find((c) => c.status === "active") ?? campaigns[0];
    if (active) {
      setActiveCampaignId(active.id);
    }
  }, [ready, campaigns, activeCampaignId]);

  if (!ready) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--cream)" }}>
        <div className="text-center space-y-2">
          <div
            className="w-8 h-8 border-2 border-t-transparent rounded-full animate-spin mx-auto"
            style={{ borderColor: "var(--terra)", borderTopColor: "transparent" }}
          />
          <p className="text-sm" style={{ color: "var(--ink-50)" }}>Đang tải...</p>
        </div>
      </div>
    );
  }

  if (!activeCampaignId) {
    return (
      <SetupWizard
        onComplete={(id) => {
          setActiveCampaignId(id);
        }}
      />
    );
  }

  return <App campaignId={activeCampaignId} />;
}
