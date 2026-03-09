"use client";

import { useCallback, useEffect, useState } from "react";
import { Search } from "lucide-react";
import { toast } from "sonner";
import { useListingStore } from "@/stores/listing-store";
import { useResearchStore } from "@/stores/research-store";
import { useCampaignStore } from "@/stores/campaign-store";
import { useScanStreamStore } from "@/stores/scan-stream-store";
import { useActivityStore } from "@/stores/activity-store";
import * as api from "@/lib/api";
import type { Listing } from "@/types";
import { CardStack } from "./card-stack";
import { ActionBar } from "./action-bar";
import { EmptyDiscover } from "./empty-discover";
import { ScanLiveSheet } from "./scan-live-sheet";

interface DiscoverScreenProps {
  campaignId: string;
}

export function DiscoverScreen({ campaignId }: DiscoverScreenProps) {
  const { listings, fetchListings } = useListingStore();
  const { researching, researchByListing } = useResearchStore();
  const { campaign } = useCampaignStore();
  const scanStatus = useScanStreamStore((s) => s.status);
  const { isScanning, triggerScan } = useActivityStore();
  const [scanSheetOpen, setScanSheetOpen] = useState(false);
  const [removing, setRemoving] = useState<Set<string>>(new Set());

  // Only new listings
  const newListings = listings.filter(
    (l) => l.stage === "new" && !removing.has(l.id)
  );

  // Show scan indicator if scanning or recent stream
  const showScanIndicator = isScanning || scanStatus === "streaming" || scanStatus === "connecting";

  // Auto-open scan sheet when scanning starts
  useEffect(() => {
    if (showScanIndicator) {
      // Don't auto-open; let user tap the indicator
    }
  }, [showScanIndicator]);

  // Auto-dismiss sheet and show toast when scan completes
  useEffect(() => {
    if (scanStatus === "complete") {
      setScanSheetOpen(false);
      const count = useScanStreamStore.getState().listingsFound;
      toast.success(`Quét xong · ${count} tin mới`, { icon: "✓" });
      fetchListings(campaignId);
    }
  }, [scanStatus, campaignId, fetchListings]);

  const getResearch = useCallback(
    (listing: Listing) => {
      const researchId = listing.research_id ?? researchByListing[listing.id];
      if (!researchId) return null;
      return researching[researchId] ?? null;
    },
    [researching, researchByListing]
  );

  const handleSwipe = useCallback(
    async (listing: Listing, direction: "like" | "skip" | "contact") => {
      // Optimistic: remove card immediately
      setRemoving((prev) => new Set([...prev, listing.id]));

      try {
        if (direction === "like") {
          // Trigger research — moves listing to "researching"
          const result = await api.triggerResearch(campaignId, {
            listing_ids: [listing.id],
            criteria: [], // use backend defaults
          });
          // Optimistically wire research_id
          if (result.research_ids[0]) {
            useListingStore.setState((s) => ({
              listings: s.listings.map((l) =>
                l.id === listing.id
                  ? { ...l, stage: "researching", research_id: result.research_ids[0] }
                  : l
              ),
            }));
          }
          // Refetch research data
          await useResearchStore.getState().fetchAllResearch(campaignId);
        } else if (direction === "contact") {
          await api.updateListing(campaignId, listing.id, { stage: "contacted" });
          useListingStore.setState((s) => ({
            listings: s.listings.map((l) =>
              l.id === listing.id ? { ...l, stage: "contacted" } : l
            ),
          }));
        } else if (direction === "skip") {
          await api.updateListing(campaignId, listing.id, {
            stage: "skipped",
            skip_reason: "other",
          });
          useListingStore.setState((s) => ({
            listings: s.listings.map((l) =>
              l.id === listing.id ? { ...l, stage: "skipped" } : l
            ),
          }));
        }
      } catch {
        // Revert: put card back
        setRemoving((prev) => {
          const next = new Set(prev);
          next.delete(listing.id);
          return next;
        });
        toast.error("Có lỗi xảy ra, thử lại nhé");
      }
    },
    [campaignId]
  );

  const handleTriggerScan = async () => {
    try {
      await triggerScan(campaignId);
      setScanSheetOpen(true);
    } catch {
      toast.error("Không thể bắt đầu quét");
    }
  };

  // Build search pill text from campaign preferences
  const prefs = campaign?.preferences;
  const pillParts: string[] = [];
  if (prefs?.district) pillParts.push(prefs.district);
  if (prefs?.bedrooms) pillParts.push(`${prefs.bedrooms}PN`);
  if (prefs?.max_price)
    pillParts.push(`≤ ${Math.round(prefs.max_price / 1_000_000)}tr`);
  const pillText = pillParts.join(" · ") || "Tất cả";

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: "var(--cream)" }}>
      {/* Header */}
      <div
        className="flex-shrink-0 flex items-center justify-between"
        style={{ padding: "12px 20px" }}
      >
        {/* Search pill */}
        <div
          className="flex items-center gap-2 px-3 py-2 text-[13px] font-medium"
          style={{
            background: "var(--ds-white)",
            borderRadius: "var(--r-full)",
            border: "1px solid var(--ink-08)",
            color: "var(--ink-70)",
            maxWidth: "60%",
          }}
        >
          <Search size={13} style={{ color: "var(--ink-30)", flexShrink: 0 }} />
          <span className="truncate">{pillText}</span>
        </div>

        {/* Right side: scan indicator or count badge */}
        <div className="flex items-center gap-2">
          {showScanIndicator ? (
            <button
              onClick={() => setScanSheetOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-semibold"
              style={{
                background: "var(--amber-15)",
                color: "var(--amber)",
                borderRadius: "var(--r-full)",
              }}
            >
              <span className="pulse-dot">●</span> Đang quét...
            </button>
          ) : (
            <>
              <div
                className="px-2.5 py-1 text-[12px] font-semibold text-white"
                style={{ background: "var(--terra)", borderRadius: "var(--r-full)" }}
              >
                {newListings.length} mới
              </div>
              {/* "Quét ngay" replaces filter icon — filter icon is Phase 2 */}
              <button
                onClick={handleTriggerScan}
                className="px-3 py-1.5 text-[12px] font-semibold"
                style={{
                  background: "var(--ink-08)",
                  color: "var(--ink-50)",
                  borderRadius: "var(--r-full)",
                }}
              >
                Quét ngay
              </button>
            </>
          )}
        </div>
      </div>

      {/* Card stack or empty state */}
      {newListings.length === 0 ? (
        <EmptyDiscover />
      ) : (
        <>
          <div className="flex-1 relative overflow-hidden" style={{ padding: "0 16px" }}>
            <CardStack
              listings={newListings}
              getResearch={getResearch}
              onSwipe={handleSwipe}
            />
          </div>

          <ActionBar
            onSkip={() => newListings[0] && handleSwipe(newListings[0], "skip")}
            onLike={() => newListings[0] && handleSwipe(newListings[0], "like")}
            onContact={() => newListings[0] && handleSwipe(newListings[0], "contact")}
          />
        </>
      )}

      {/* Scan live sheet */}
      <ScanLiveSheet open={scanSheetOpen} onClose={() => setScanSheetOpen(false)} />
    </div>
  );
}
