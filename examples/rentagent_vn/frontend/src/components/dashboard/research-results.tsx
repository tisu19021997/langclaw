"use client";

import { useState } from "react";
import {
  MapPin,
  RefreshCw,
  ChevronDown,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ScoreBadge } from "./score-badge";
import { CriteriaScores } from "./criteria-scores";
import type { AreaResearch } from "@/types";
import { useListingStore } from "@/stores/listing-store";
import { useResearchStore } from "@/stores/research-store";

interface ResearchResultsProps {
  research: AreaResearch;
  campaignId: string;
  listingId: string;
}

export function ResearchResults({
  research,
  campaignId,
  listingId,
}: ResearchResultsProps) {
  const [activeTab, setActiveTab] = useState<"overview" | "details" | "street">(
    "overview"
  );
  const { fetchListings } = useListingStore();
  const { retryResearch } = useResearchStore();

  if (research.status !== "done" || !research.scores) return null;

  const { scores, verdict, overall_score } = research;

  const handleRetry = async () => {
    await retryResearch(campaignId, research.id);
    await fetchListings(campaignId);
  };

  const tabs = [
    { key: "overview" as const, label: "Overview" },
    { key: "details" as const, label: "Details" },
    ...(research.street_view_urls.length > 0
      ? [{ key: "street" as const, label: "Street View" }]
      : []),
  ];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <MapPin className="h-4 w-4 text-teal-500" />
          <span className="text-sm font-medium">Research results</span>
        </div>
        {overall_score != null && <ScoreBadge score={overall_score} size="lg" />}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 rounded-md bg-muted p-0.5">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex-1 rounded-sm px-2 py-1 text-xs font-medium transition-colors ${
              activeTab === tab.key
                ? "bg-background shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab: Overview */}
      {activeTab === "overview" && scores && (
        <div className="space-y-4">
          <CriteriaScores scores={scores} />

          {verdict && (
            <>
              <Separator />
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">
                  Verdict
                </p>
                <p className="text-sm leading-relaxed">{verdict}</p>
              </div>
            </>
          )}
        </div>
      )}

      {/* Tab: Details */}
      {activeTab === "details" && scores && (
        <div className="space-y-2">
          {scores.criteria.map((criterion) => (
            <Collapsible key={criterion.criterion_key}>
              <CollapsibleTrigger className="flex w-full items-center justify-between rounded-md p-2 hover:bg-muted/50 text-left">
                <div className="flex items-center gap-2">
                  <ScoreBadge score={criterion.score} size="sm" />
                  <span className="text-sm font-medium">{criterion.label}</span>
                  {criterion.walking_distance && (
                    <Badge
                      variant="outline"
                      className="text-[10px] h-4 px-1 text-green-600"
                    >
                      Walking
                    </Badge>
                  )}
                </div>
                <ChevronDown className="h-4 w-4 text-muted-foreground" />
              </CollapsibleTrigger>
              <CollapsibleContent className="px-2 pb-2">
                {/* Highlights */}
                {criterion.highlights.length > 0 && (
                  <ul className="space-y-1 mt-2">
                    {criterion.highlights.map((h, i) => (
                      <li
                        key={i}
                        className="text-xs text-muted-foreground flex gap-1.5"
                      >
                        <span className="text-teal-500 mt-0.5 shrink-0">
                          &bull;
                        </span>
                        {h}
                      </li>
                    ))}
                  </ul>
                )}

                {/* Detail key-values */}
                {criterion.details.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {criterion.details.map((detail, idx) => (
                      <div key={idx} className="flex gap-2 text-xs">
                        <span className="text-muted-foreground font-medium min-w-[80px] capitalize">
                          {detail.key}:
                        </span>
                        <span className="text-foreground">{detail.value}</span>
                      </div>
                    ))}
                  </div>
                )}
              </CollapsibleContent>
            </Collapsible>
          ))}
        </div>
      )}

      {/* Tab: Street View */}
      {activeTab === "street" && research.street_view_urls.length > 0 && (
        <div className="space-y-2">
          {research.street_view_urls.map((url, i) => (
            <a
              key={i}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 p-2 rounded-md border hover:bg-muted/50 text-sm"
            >
              <ExternalLink className="h-4 w-4 text-muted-foreground" />
              <span className="truncate">Street View {i + 1}</span>
            </a>
          ))}
        </div>
      )}

      <Separator />

      {/* Action buttons */}
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handleRetry}
        >
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Research again
        </Button>
      </div>
    </div>
  );
}
