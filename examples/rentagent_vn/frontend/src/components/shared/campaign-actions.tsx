"use client";

import { useEffect, useState } from "react";
import { Archive } from "lucide-react";
import type { Campaign } from "@/types";
import { deriveCampaignName } from "./campaign-pill";

interface CampaignActionsProps {
  open: boolean;
  onClose: () => void;
  campaign: Campaign | null;
  onArchive: (campaignId: string) => Promise<void>;
}

export function CampaignActions({
  open,
  onClose,
  campaign,
  onArchive,
}: CampaignActionsProps) {
  const [isVisible, setIsVisible] = useState(false);
  const [isAnimating, setIsAnimating] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [isArchiving, setIsArchiving] = useState(false);

  useEffect(() => {
    if (open) {
      setIsVisible(true);
      setShowConfirm(false);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setIsAnimating(true);
        });
      });
    } else {
      setIsAnimating(false);
      const timer = setTimeout(() => {
        setIsVisible(false);
        setShowConfirm(false);
      }, 200);
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

  const handleArchive = async () => {
    if (!campaign) return;

    setIsArchiving(true);
    try {
      await onArchive(campaign.id);
      onClose();
    } finally {
      setIsArchiving(false);
    }
  };

  if (!isVisible || !campaign) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 transition-opacity duration-200"
        style={{
          background: "rgba(0,0,0,0.3)",
          opacity: isAnimating ? 1 : 0,
        }}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Action sheet */}
      <div
        className="fixed left-0 right-0 bottom-0 z-50 transition-transform duration-200"
        style={{
          background: "var(--ds-white)",
          borderRadius: "var(--r-lg) var(--r-lg) 0 0",
          padding: "20px 20px calc(32px + env(safe-area-inset-bottom, 0px))",
          transform: isAnimating ? "translateY(0)" : "translateY(100%)",
        }}
      >
        {!showConfirm ? (
          <>
            {/* Campaign name header */}
            <p
              className="text-[11px] font-semibold uppercase mb-4"
              style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
            >
              {deriveCampaignName(campaign)}
            </p>

            {/* Archive action */}
            <button
              onClick={() => setShowConfirm(true)}
              className="w-full flex items-center gap-3 py-3 px-4 rounded-xl transition-colors hover:bg-[var(--ink-04)]"
            >
              <div
                className="flex items-center justify-center"
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: "var(--r-sm)",
                  background: "var(--ink-08)",
                }}
              >
                <Archive size={18} style={{ color: "var(--ink-50)" }} />
              </div>
              <span
                className="text-[15px] font-medium"
                style={{ color: "var(--ink)" }}
              >
                Archive
              </span>
            </button>
          </>
        ) : (
          <>
            {/* Confirmation view */}
            <p
              className="text-[15px] font-medium mb-4"
              style={{ color: "var(--ink)" }}
            >
              Archive this campaign? Data will be kept but won&apos;t appear in
              your list.
            </p>

            <div className="flex gap-3">
              <button
                onClick={onClose}
                disabled={isArchiving}
                className="flex-1 py-3 px-4 text-[15px] font-semibold rounded-xl transition-colors"
                style={{
                  background: "var(--ink-08)",
                  color: "var(--ink-50)",
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleArchive}
                disabled={isArchiving}
                className="flex-1 py-3 px-4 text-[15px] font-semibold text-white rounded-xl transition-colors disabled:opacity-50"
                style={{
                  background: "var(--terra)",
                }}
              >
                {isArchiving ? "Archiving..." : "Archive"}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
