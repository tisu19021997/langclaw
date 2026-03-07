"use client";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { ListingCard } from "./listing-card";
import { useResearchStore } from "@/stores/research-store";
import { cn } from "@/lib/utils";
import type { Listing, PipelineStage } from "@/types";

interface PipelineColumnProps {
  stage: { key: PipelineStage; label: string; color: string };
  listings: Listing[];
  campaignId: string;
  hasRunning?: boolean;
}

export function PipelineColumn({
  stage,
  listings,
  campaignId,
  hasRunning,
}: PipelineColumnProps) {
  const { selectedIds, selectAll, clearSelection } = useResearchStore();
  const isNewColumn = stage.key === "new";
  const isResearchingColumn = stage.key === "researching";
  const allSelected =
    isNewColumn &&
    listings.length > 0 &&
    listings.every((l) => selectedIds.has(l.id));

  const handleSelectAll = () => {
    if (allSelected) {
      clearSelection();
    } else {
      selectAll(listings.map((l) => l.id));
    }
  };

  return (
    <div
      className={cn(
        "flex flex-col min-w-[280px] w-[280px] h-full rounded-lg overflow-hidden",
        isResearchingColumn
          ? "bg-teal-50/30 dark:bg-teal-950/20 border-l-2 border-l-teal-500"
          : "bg-muted/30"
      )}
    >
      {/* Column header - fixed */}
      <div
        className={cn(
          "flex-shrink-0 flex items-center justify-between px-3 py-2.5 border-b",
          isResearchingColumn
            ? "bg-teal-50/50 dark:bg-teal-950/30"
            : "bg-muted/30"
        )}
      >
        <div className="flex items-center gap-2">
          {isNewColumn && listings.length > 0 && (
            <Checkbox
              checked={allSelected}
              onCheckedChange={handleSelectAll}
              className="mr-0.5"
            />
          )}
          {/* Pulsing dot when research is running */}
          {hasRunning && isResearchingColumn && (
            <span className="h-2 w-2 rounded-full bg-teal-500 animate-pulse" />
          )}
          <div className={`w-2 h-2 rounded-full ${stage.color}`} />
          <span className="text-sm font-medium">{stage.label}</span>
        </div>
        <Badge variant="secondary" className="text-xs h-5 px-1.5">
          {listings.length}
        </Badge>
      </div>

      {/* Cards - scrollable */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-2 space-y-2">
          {listings.length === 0 ? (
            <div className="flex items-center justify-center h-32 m-2 border-2 border-dashed border-muted-foreground/20 rounded-md">
              <p className="text-xs text-muted-foreground">Chưa có căn nào</p>
            </div>
          ) : (
            listings.map((listing) => (
              <ListingCard
                key={listing.id}
                listing={listing}
                campaignId={campaignId}
                selectable={isNewColumn}
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
