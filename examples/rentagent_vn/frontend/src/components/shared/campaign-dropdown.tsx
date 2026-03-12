"use client";

import { useEffect, useRef, useState } from "react";
import { MoreHorizontal, Plus } from "lucide-react";
import type { Campaign, CampaignStats } from "@/types";
import { deriveCampaignName } from "./campaign-pill";

interface CampaignDropdownProps {
  open: boolean;
  onClose: () => void;
  campaigns: Campaign[];
  activeCampaignId: string;
  stats: Record<string, CampaignStats | null>;
  scanningCampaignIds: Set<string>;
  onSelect: (campaignId: string) => void;
  onCreate: () => void;
  onOpenActions: (campaign: Campaign) => void;
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays === 1) return "yesterday";
  return `${diffDays} days ago`;
}

export function CampaignDropdown({
  open,
  onClose,
  campaigns,
  activeCampaignId,
  stats,
  scanningCampaignIds,
  onSelect,
  onCreate,
  onOpenActions,
}: CampaignDropdownProps) {
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [isAnimating, setIsAnimating] = useState(false);

  useEffect(() => {
    if (open) {
      setIsVisible(true);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setIsAnimating(true);
        });
      });
    } else {
      setIsAnimating(false);
      const timer = setTimeout(() => setIsVisible(false), 200);
      return () => clearTimeout(timer);
    }
  }, [open]);

  useEffect(() => {
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape" && open) {
        onClose();
      }
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [open, onClose]);

  if (!isVisible) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 transition-opacity duration-200"
        style={{
          background: "rgba(0,0,0,0.3)",
          opacity: isAnimating ? 1 : 0,
        }}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Dropdown */}
      <div
        ref={dropdownRef}
        className="fixed z-50 transition-all duration-200"
        style={{
          top: 56,
          left: 20,
          width: "min(90vw, 340px)",
          maxHeight: "60vh",
          background: "var(--ds-white)",
          border: "1px solid var(--ink-08)",
          borderRadius: "var(--r-lg)",
          boxShadow: "var(--shadow-float)",
          padding: 16,
          overflowY: "auto",
          opacity: isAnimating ? 1 : 0,
          transform: isAnimating ? "translateY(0)" : "translateY(-8px)",
        }}
      >
        {/* Section label */}
        <p
          className="text-[11px] font-semibold uppercase mb-3"
          style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
        >
          Your Campaigns
        </p>

        {/* Campaign list */}
        <div className="space-y-0">
          {campaigns.map((campaign, index) => {
            const isActive = campaign.id === activeCampaignId;
            const campaignStats = stats[campaign.id];
            const isScanning = scanningCampaignIds.has(campaign.id);
            const listingCount = campaignStats?.total_listings ?? 0;

            let statusText: string;
            if (isScanning) {
              statusText = "Scanning";
            } else if (campaignStats && campaignStats.total_scans > 0) {
              statusText = `Last scan ${formatRelativeTime(campaign.updated_at)}`;
            } else {
              statusText = "Never scanned";
            }

            return (
              <div
                key={campaign.id}
                className="flex items-center gap-3 py-3"
                style={{
                  borderBottom:
                    index < campaigns.length - 1
                      ? "1px solid var(--ink-04)"
                      : "none",
                }}
              >
                {/* Radio indicator */}
                <button
                  onClick={() => onSelect(campaign.id)}
                  className="flex-shrink-0 flex items-center justify-center"
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: "50%",
                    border: `2px solid ${isActive ? "var(--terra)" : "var(--ink-15)"}`,
                    background: isActive ? "var(--terra)" : "transparent",
                  }}
                  aria-label={`Select ${deriveCampaignName(campaign)}`}
                >
                  {isActive && (
                    <div
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: "white",
                      }}
                    />
                  )}
                </button>

                {/* Content */}
                <button
                  onClick={() => onSelect(campaign.id)}
                  className="flex-1 min-w-0 text-left"
                >
                  <p
                    className="text-[14px] font-semibold truncate"
                    style={{ color: "var(--ink)" }}
                  >
                    {deriveCampaignName(campaign)}
                  </p>
                  <p
                    className="text-[12px] mt-0.5"
                    style={{ color: "var(--ink-50)" }}
                  >
                    {listingCount} listings · {statusText}
                  </p>
                </button>

                {/* Overflow menu */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenActions(campaign);
                  }}
                  className="flex-shrink-0 p-1 rounded-md transition-colors hover:bg-[var(--ink-04)]"
                  aria-label="Campaign actions"
                >
                  <MoreHorizontal size={20} style={{ color: "var(--ink-30)" }} />
                </button>
              </div>
            );
          })}
        </div>

        {/* Create new button */}
        <button
          onClick={() => {
            onClose();
            onCreate();
          }}
          className="w-full flex items-center justify-center gap-2 mt-4 py-3.5 px-4 transition-colors"
          style={{
            background: "var(--terra-08)",
            border: "1px dashed var(--terra)",
            borderRadius: "var(--r-lg)",
            color: "var(--terra)",
          }}
        >
          <Plus size={18} />
          <span className="text-[14px] font-semibold">Create new campaign</span>
        </button>
      </div>
    </>
  );
}
