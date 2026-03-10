"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { TrackCard } from "./track-card";
import type { Listing } from "@/types";

interface TrackSectionProps {
  title: string;
  dotColor: string;
  listings: Listing[];
  collapsedByDefault?: boolean;
  campaignId: string;
}

export function TrackSection({
  title,
  dotColor,
  listings,
  collapsedByDefault = false,
  campaignId,
}: TrackSectionProps) {
  const [collapsed, setCollapsed] = useState(collapsedByDefault);

  if (listings.length === 0) return null;

  return (
    <div className="space-y-2">
      {/* Section header */}
      <button
        onClick={() => collapsedByDefault && setCollapsed((c) => !c)}
        className="w-full flex items-center justify-between px-1"
        style={{ cursor: collapsedByDefault ? "pointer" : "default" }}
      >
        <div className="flex items-center gap-2">
          <div
            className="rounded-full flex-shrink-0"
            style={{ width: 8, height: 8, background: dotColor }}
          />
          <span
            className="text-[13px] font-semibold"
            style={{ color: "var(--ink)", letterSpacing: "-0.2px" }}
          >
            {title}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[12px]" style={{ color: "var(--ink-30)" }}>
            {listings.length} căn
          </span>
          {collapsedByDefault &&
            (collapsed ? (
              <ChevronDown size={14} style={{ color: "var(--ink-30)" }} />
            ) : (
              <ChevronUp size={14} style={{ color: "var(--ink-30)" }} />
            ))}
        </div>
      </button>

      {/* Cards */}
      {!collapsed && (
        <div className="space-y-2">
          {listings.map((listing) => (
            <TrackCard key={listing.id} listing={listing} campaignId={campaignId} />
          ))}
        </div>
      )}
    </div>
  );
}
