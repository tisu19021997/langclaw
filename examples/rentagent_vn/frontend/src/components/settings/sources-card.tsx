"use client";

import { useState } from "react";
import { useCampaignStore } from "@/stores/campaign-store";
import { SourceIcon, getPlatformFromUrl, type SourcePlatform } from "@/components/shared";
import { SourcesSheet } from "./sources-sheet";

interface SourcesCardProps {
  campaignId: string;
}

export function SourcesCard({ campaignId }: SourcesCardProps) {
  const [sheetOpen, setSheetOpen] = useState(false);
  const campaign = useCampaignStore((s) => s.campaign);
  const sources = campaign?.sources ?? [];

  const sourcesWithPlatform = sources.map((url) => ({
    url,
    platform: getPlatformFromUrl(url),
  }));

  const displaySources = sourcesWithPlatform.slice(0, 4);
  const overflowCount = sourcesWithPlatform.length - 4;

  return (
    <>
      <div
        className="flex items-center justify-between gap-3 p-4"
        style={{
          background: "var(--ds-white)",
          borderRadius: "var(--r-lg)",
          border: "1px solid var(--ink-08)",
        }}
      >
        <div className="flex items-center gap-2 flex-1 overflow-x-auto">
          {displaySources.length > 0 ? (
            <>
              <div className="flex items-center gap-[-8px]">
                {displaySources.map((source, i) => (
                  <div
                    key={source.url}
                    style={{
                      marginLeft: i > 0 ? -8 : 0,
                      zIndex: displaySources.length - i,
                    }}
                  >
                    <SourceIcon platform={source.platform} size={32} />
                  </div>
                ))}
                {overflowCount > 0 && (
                  <div
                    className="w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-semibold"
                    style={{
                      marginLeft: -8,
                      background: "var(--ink-08)",
                      color: "var(--ink-50)",
                      zIndex: 0,
                    }}
                  >
                    +{overflowCount}
                  </div>
                )}
              </div>
              <span
                className="text-[13px] font-medium ml-2"
                style={{ color: "var(--ink-50)" }}
              >
                ({sources.length} sources)
              </span>
            </>
          ) : (
              <span className="text-[13px]" style={{ color: "var(--ink-30)" }}>
                No sources configured
              </span>
          )}
        </div>
        <button
          className="text-[13px] font-semibold flex-shrink-0"
          style={{ color: "var(--terra)" }}
          onClick={() => setSheetOpen(true)}
        >
          Edit
        </button>
      </div>

      <SourcesSheet
        campaignId={campaignId}
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
      />
    </>
  );
}
