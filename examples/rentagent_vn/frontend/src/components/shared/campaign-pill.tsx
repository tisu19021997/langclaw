"use client";

import { ChevronDown, Home } from "lucide-react";
import type { Campaign } from "@/types";

interface CampaignPillProps {
  campaign: Campaign;
  onClick: () => void;
}

function formatPrice(price: number): string {
  if (price >= 1_000_000) {
    return `${Math.round(price / 1_000_000)}M`;
  }
  return `${Math.round(price / 1_000)}K`;
}

export function deriveCampaignName(campaign: Campaign): string {
  const prefs = campaign.preferences;

  const parts: string[] = [];

  if (prefs?.district) {
    parts.push(prefs.district);
  }

  if (prefs?.min_price && prefs?.max_price) {
    parts.push(`${formatPrice(prefs.min_price)}-${formatPrice(prefs.max_price)}`);
  } else if (prefs?.max_price) {
    parts.push(`≤${formatPrice(prefs.max_price)}`);
  } else if (prefs?.min_price) {
    parts.push(`≥${formatPrice(prefs.min_price)}`);
  }

  if (parts.length > 0) {
    return parts.join(" ");
  }

  const date = new Date(campaign.created_at);
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return `Campaign ${month}/${day}`;
}

export function CampaignPill({ campaign, onClick }: CampaignPillProps) {
  const displayName = deriveCampaignName(campaign);

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 transition-colors"
      style={{
        background: "var(--ds-white)",
        border: "1px solid var(--ink-08)",
        borderRadius: "var(--r-full)",
        padding: "8px 14px 8px 10px",
        maxWidth: "min(70vw, 280px)",
      }}
      aria-label="Select campaign"
    >
      <Home
        size={16}
        style={{ color: "var(--ink-30)", flexShrink: 0 }}
      />
      <span
        className="truncate text-[13px] font-semibold"
        style={{ color: "var(--ink)" }}
      >
        {displayName}
      </span>
      <ChevronDown
        size={14}
        style={{ color: "var(--ink-30)", flexShrink: 0 }}
      />
    </button>
  );
}
