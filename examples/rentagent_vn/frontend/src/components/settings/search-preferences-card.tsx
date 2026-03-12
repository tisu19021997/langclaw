"use client";

import { useState } from "react";
import { useCampaignStore } from "@/stores/campaign-store";
import { PreferencePillsReadOnly } from "@/components/shared";
import { SearchPreferencesSheet } from "./search-preferences-sheet";

interface SearchPreferencesCardProps {
  campaignId: string;
}

export function SearchPreferencesCard({ campaignId }: SearchPreferencesCardProps) {
  const [sheetOpen, setSheetOpen] = useState(false);
  const campaign = useCampaignStore((s) => s.campaign);
  const prefs = campaign?.preferences ?? {};

  return (
    <>
      <div
        className="flex items-start justify-between gap-3 p-4"
        style={{
          background: "var(--ds-white)",
          borderRadius: "var(--r-lg)",
          border: "1px solid var(--ink-08)",
        }}
      >
        <div className="flex-1">
          <PreferencePillsReadOnly preferences={prefs} />
        </div>
        <button
          className="text-[13px] font-semibold flex-shrink-0"
          style={{ color: "var(--terra)" }}
          onClick={() => setSheetOpen(true)}
        >
          Edit
        </button>
      </div>

      <SearchPreferencesSheet
        campaignId={campaignId}
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
      />
    </>
  );
}
