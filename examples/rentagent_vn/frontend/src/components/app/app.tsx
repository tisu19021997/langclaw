"use client";

import { useState, useEffect } from "react";
import { Heart, LayoutGrid, Settings } from "lucide-react";
import { useCampaignStore } from "@/stores/campaign-store";
import { useListingStore } from "@/stores/listing-store";
import { useResearchStore } from "@/stores/research-store";
import { useActivityStore } from "@/stores/activity-store";
import { useScanStreamStore } from "@/stores/scan-stream-store";
import { useCampaign } from "@/hooks/use-campaign";
import { useScanStream } from "@/hooks/use-scan-stream";
import { useResearchStream } from "@/hooks/use-research-stream";
import { DiscoverScreen } from "@/components/discover/discover-screen";
import { TrackScreen } from "@/components/track/track-screen";
import { SettingsScreen } from "@/components/settings/settings-screen";

type Tab = "discover" | "track" | "settings";

const TABS: { key: Tab; icon: typeof Heart; label: string }[] = [
  { key: "discover", icon: Heart, label: "Khám phá" },
  { key: "track", icon: LayoutGrid, label: "Theo dõi" },
  { key: "settings", icon: Settings, label: "Cài đặt" },
];

interface AppProps {
  campaignId: string;
}

export function App({ campaignId }: AppProps) {
  const [activeTab, setActiveTab] = useState<Tab>("discover");
  const { campaign, fetchStats } = useCampaignStore();
  const { fetchListings } = useListingStore();
  const { isScanning, latestScan, fetchScans, fetchActivities } =
    useActivityStore();
  const scanStatus = useScanStreamStore((s) => s.status);
  const { fetchAllResearch, researching } = useResearchStore();

  const hasActiveResearch = Object.values(researching).some(
    (r) => r.status === "queued" || r.status === "running"
  );

  // Load campaign data + poll
  useCampaign(campaignId);

  // Connect SSE when scanning
  useScanStream(campaignId, isScanning && latestScan ? latestScan.id : null);

  // Connect research SSE when research is active
  useResearchStream(campaignId, hasActiveResearch);

  // Refetch data when scan completes
  useEffect(() => {
    if (scanStatus === "complete") {
      fetchListings(campaignId);
      fetchStats(campaignId);
      fetchScans(campaignId);
      fetchActivities(campaignId);
    }
  }, [scanStatus, campaignId, fetchListings, fetchStats, fetchScans, fetchActivities]);

  // Load research data on mount
  useEffect(() => {
    fetchAllResearch(campaignId);
  }, [campaignId, fetchAllResearch]);

  if (!campaign) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--cream)" }}>
        <div className="animate-pulse" style={{ color: "var(--ink-50)" }}>
          Đang tải...
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden" style={{ background: "var(--cream)" }}>
      {/* Active screen */}
      <div className="flex-1 overflow-hidden relative">
        {activeTab === "discover" && <DiscoverScreen campaignId={campaignId} />}
        {activeTab === "track" && <TrackScreen campaignId={campaignId} />}
        {activeTab === "settings" && <SettingsScreen campaignId={campaignId} />}
      </div>

      {/* Bottom nav */}
      <nav
        className="flex-shrink-0 flex items-center justify-around"
        style={{
          height: 80,
          borderTop: "1px solid var(--ink-08)",
          background: "var(--cream)",
          paddingBottom: "env(safe-area-inset-bottom, 0px)",
        }}
      >
        {TABS.map(({ key, icon: Icon, label }) => {
          const isActive = activeTab === key;
          return (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className="flex flex-col items-center gap-1 py-2 px-4"
              style={{ color: isActive ? "var(--terra)" : "var(--ink-30)" }}
            >
              <Icon size={22} fill={key === "discover" && isActive ? "var(--terra)" : "none"} />
              <span className="text-[11px] font-medium">{label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
