"use client";

import { useState } from "react";
import { useResearchStore } from "@/stores/research-store";
import { ListingDetailSheet } from "@/components/listing";
import type { Listing } from "@/types";

interface TrackCardProps {
  listing: Listing;
  campaignId: string;
}

function ResearchBadge({ listing }: { listing: Listing }) {
  const { researching, researchByListing } = useResearchStore();
  const researchId = listing.research_id ?? researchByListing[listing.id];
  const research = researchId ? researching[researchId] : null;

  if (listing.stage === "contacted" || listing.stage === "viewing") {
    return (
      <div
        className="px-2 py-0.5 text-[11px] font-semibold rounded-full"
        style={{ background: "var(--terra-15)", color: "var(--terra)" }}
      >
        Contacted
      </div>
    );
  }

  if (!research) {
    if (listing.stage === "researching") {
      return (
        <div
          className="flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold rounded-full"
          style={{ background: "var(--amber-15)", color: "var(--amber)" }}
        >
          <span className="pulse-dot">●</span> Researching
        </div>
      );
    }
    return null;
  }

  if (research.status === "queued" || research.status === "running") {
    return (
      <div
        className="flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold rounded-full"
        style={{ background: "var(--amber-15)", color: "var(--amber)" }}
      >
        <span className="pulse-dot">●</span> Researching
      </div>
    );
  }

  if (research.status === "done" && research.overall_score !== null) {
    return (
      <div
        className="px-2 py-0.5 text-[11px] font-semibold rounded-full"
        style={{ background: "var(--jade-15)", color: "var(--jade)" }}
      >
        ★ {research.overall_score.toFixed(1)}
      </div>
    );
  }

  if (research.status === "failed") {
    return (
      <div
        className="px-2 py-0.5 text-[11px] font-semibold rounded-full"
        style={{ background: "#fde8e8", color: "#cc0033" }}
      >
        Error
      </div>
    );
  }

  return null;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr + "Z").getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function TrackCard({ listing, campaignId }: TrackCardProps) {
  const { researching, researchByListing, liveState } = useResearchStore();
  const researchId = listing.research_id ?? researchByListing[listing.id];
  const research = researchId ? researching[researchId] : null;
  const live = researchId ? liveState[researchId] : null;
  const [detailOpen, setDetailOpen] = useState(false);

  const isResearchRunning =
    listing.stage === "researching" && research?.status === "running";
  const browserUrl = live?.browserUrl;

  const handleTap = () => {
    setDetailOpen(true);
  };

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        onClick={handleTap}
        onKeyDown={(e) => e.key === "Enter" && handleTap()}
        className="w-full flex items-center gap-3 text-left cursor-pointer"
        style={{
          background: "var(--ds-white)",
          border: "1px solid var(--ink-08)",
          borderRadius: "var(--r-lg)",
          padding: 12,
          boxShadow: "var(--shadow-card)",
        }}
      >
        {/* Thumbnail */}
        <div
          className="flex-shrink-0 bg-center bg-cover"
          style={{
            width: 78,
            height: 78,
            borderRadius: "var(--r-md)",
            backgroundColor: "var(--cream-200)",
            backgroundImage: listing.thumbnail_url
              ? `url(${listing.thumbnail_url})`
              : undefined,
          }}
        />

        {/* Content */}
        <div className="flex-1 min-w-0 flex flex-col gap-1">
          <div
            className="text-[13px] font-semibold truncate"
            style={{ color: "var(--ink)" }}
          >
            {listing.title || listing.address || "Apartment"}
          </div>
          <div
            className="text-[13px] font-bold"
            style={{ color: "var(--terra)" }}
          >
            {listing.price_display || "Contact"}
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {listing.bedrooms !== null && (
              <span
                className="text-[11px] px-1.5 py-0.5 rounded"
                style={{ background: "var(--ink-04)", color: "var(--ink-50)" }}
              >
                {listing.bedrooms}BR
              </span>
            )}
            {listing.area_sqm !== null && (
              <span
                className="text-[11px] px-1.5 py-0.5 rounded"
                style={{ background: "var(--ink-04)", color: "var(--ink-50)" }}
              >
                {listing.area_sqm}m²
              </span>
            )}
            {listing.district && (
              <span
                className="text-[11px] px-1.5 py-0.5 rounded"
                style={{ background: "var(--ink-04)", color: "var(--ink-50)" }}
              >
                {listing.district}
              </span>
            )}
          </div>
        </div>

        {/* Inline live preview */}
        {isResearchRunning && browserUrl && (
          <div
            className="flex-shrink-0 overflow-hidden relative"
            style={{
              width: 120,
              height: 80,
              borderRadius: "var(--r-md)",
              background: "var(--cream-100)",
            }}
          >
            <iframe
              src={browserUrl}
              className="absolute inset-0 border-0 pointer-events-none"
              style={{
                width: "100%",
                height: "100%",
                transform: "scale(1.5)",
                transformOrigin: "top center",
              }}
              sandbox="allow-scripts allow-same-origin"
              title="Research live preview"
            />
          </div>
        )}

        {/* Right column */}
        <div className="flex-shrink-0 flex flex-col items-end gap-1.5">
          <ResearchBadge listing={listing} />
          <span className="text-[11px]" style={{ color: "var(--ink-30)" }}>
            {timeAgo(listing.updated_at)}
          </span>
        </div>
      </div>

      {/* Listing detail sheet */}
      <ListingDetailSheet
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        listing={listing}
        campaignId={campaignId}
        mode="track"
      />
    </>
  );
}
