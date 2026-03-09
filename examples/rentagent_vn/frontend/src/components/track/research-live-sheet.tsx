"use client";

import { useEffect, useRef } from "react";
import { useResearchStore } from "@/stores/research-store";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import type { Listing } from "@/types";

interface ResearchLiveSheetProps {
  open: boolean;
  onClose: () => void;
  listing: Listing;
  researchId: string;
}

export function ResearchLiveSheet({
  open,
  onClose,
  listing,
  researchId,
}: ResearchLiveSheetProps) {
  const { liveState, researching } = useResearchStore();
  const live = liveState[researchId];
  const research = researching[researchId];
  const stepLogRef = useRef<HTMLDivElement>(null);

  // Collect step log entries from liveState history
  // We build a running list from step updates
  const currentStep = live?.currentStep;
  const currentDetail = live?.currentDetail;
  const browserUrl = live?.browserUrl;
  const isDone = research?.status === "done";
  const finalScore = research?.overall_score;

  // Auto-scroll step log to bottom
  useEffect(() => {
    if (stepLogRef.current) {
      stepLogRef.current.scrollTop = stepLogRef.current.scrollHeight;
    }
  }, [currentStep, currentDetail]);

  const listingName =
    listing.title || listing.address || "Căn hộ này";

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title={listingName}
      subtitle="Đang khảo sát khu vực"
      maxHeight="90vh"
    >
      {/* iframe — 55% height of sheet */}
      <div className="flex-shrink-0" style={{ height: "55%" }}>
        {isDone && finalScore !== null ? (
          // Show final score when done
          <div
            className="w-full h-full flex flex-col items-center justify-center gap-3"
            style={{ background: "var(--jade-15)" }}
          >
            <div
              className="text-[60px] font-black"
              style={{ color: "var(--jade)", lineHeight: 1 }}
            >
              {finalScore.toFixed(1)}
            </div>
            <div className="text-[16px] font-semibold" style={{ color: "var(--jade)" }}>
              Khảo sát hoàn thành
            </div>
            {research.verdict && (
              <p
                className="text-[13px] text-center px-8"
                style={{ color: "var(--jade)" }}
              >
                {research.verdict}
              </p>
            )}
          </div>
        ) : browserUrl ? (
          <iframe
            src={browserUrl}
            className="w-full h-full border-0"
            sandbox="allow-scripts allow-same-origin"
            title="Research live preview"
          />
        ) : (
          <div
            className="w-full h-full flex flex-col items-center justify-center gap-2"
            style={{ background: "var(--cream-100)" }}
          >
            <div
              className="w-8 h-8 border-2 border-t-transparent rounded-full animate-spin"
              style={{ borderColor: "var(--amber)", borderTopColor: "transparent" }}
            />
            <p className="text-[13px]" style={{ color: "var(--ink-50)" }}>
              Đang khởi động...
            </p>
          </div>
        )}
      </div>

      {/* Step log — remaining height */}
      <div
        ref={stepLogRef}
        className="flex-1 overflow-y-auto px-5 py-3 space-y-2"
        style={{ borderTop: "1px solid var(--ink-04)" }}
      >
        {currentStep ? (
          <div>
            <div
              className="text-[12px] font-semibold"
              style={{ color: "var(--ink)" }}
            >
              {currentStep}
            </div>
            {currentDetail && (
              <div
                className="text-[11px] mt-0.5"
                style={{ color: "var(--ink-50)" }}
              >
                {currentDetail}
              </div>
            )}
          </div>
        ) : (
          <p className="text-[12px]" style={{ color: "var(--ink-30)" }}>
            {isDone ? "Hoàn thành." : "Đang chờ dữ liệu..."}
          </p>
        )}
      </div>
    </BottomSheet>
  );
}
