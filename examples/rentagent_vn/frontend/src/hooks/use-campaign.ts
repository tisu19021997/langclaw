"use client";

import { useEffect } from "react";
import { useCampaignStore } from "@/stores/campaign-store";
import { useListingStore } from "@/stores/listing-store";
import { useActivityStore } from "@/stores/activity-store";

/**
 * Hook that loads campaign data on mount and sets up polling.
 *
 * Uses Zustand selectors to extract ONLY the necessary functions.
 * This prevents the component from re-rendering on every store update.
 *
 * Polling strategy:
 * - Always checks scan status (lightweight) every 5s
 * - Only fetches stats/listings/activities when a scan is actively running
 */
export function useCampaign(campaignId: string | null) {
  // Extract ONLY the specific actions using selectors.
  // This stops the component from subscribing to the entire store state.
  const fetchCampaign = useCampaignStore((s) => s.fetchCampaign);
  const fetchStats = useCampaignStore((s) => s.fetchStats);
  const fetchListings = useListingStore((s) => s.fetchListings);
  const fetchScans = useActivityStore((s) => s.fetchScans);
  const fetchActivities = useActivityStore((s) => s.fetchActivities);

  useEffect(() => {
    if (!campaignId) return;

    // Initial fetch — load everything once
    fetchCampaign(campaignId);
    fetchStats(campaignId);
    fetchListings(campaignId);
    fetchScans(campaignId);
    fetchActivities(campaignId);

    // Poll: always check scan status; only fetch heavy data while scanning
    const interval = setInterval(() => {
      // Always check scan status (lightweight endpoint)
      fetchScans(campaignId);

      // Read isScanning dynamically via getState() instead of selecting it.
      // This guarantees the interval always sees the latest value WITHOUT
      // causing the component to re-render every time isScanning toggles.
      const isScanning = useActivityStore.getState().isScanning;

      if (isScanning) {
        fetchStats(campaignId);
        fetchListings(campaignId);
        fetchActivities(campaignId);
      }
    }, 5000);

    return () => clearInterval(interval);
  }, [campaignId, fetchCampaign, fetchStats, fetchListings, fetchScans, fetchActivities]);
}
