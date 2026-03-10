"use client";

import { useListingStore } from "@/stores/listing-store";
import { TrackSection } from "./track-section";

interface TrackScreenProps {
  campaignId: string;
}

export function TrackScreen({ campaignId }: TrackScreenProps) {
  const { listings } = useListingStore();

  // Filter by sections per PRD stage mapping
  const researching = listings.filter((l) => l.stage === "researching");
  const contacted = listings.filter(
    (l) => l.stage === "contacted" || l.stage === "viewing"
  );
  const done = listings.filter(
    (l) => l.stage === "viewed" || l.stage === "shortlisted"
  );

  const total = researching.length + contacted.length + done.length;

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ background: "var(--cream)" }}
    >
      {/* Sticky header */}
      <div
        className="flex-shrink-0 px-5 pt-5 pb-3"
        style={{
          position: "sticky",
          top: 0,
          zIndex: 20,
          background: "var(--cream)",
          borderBottom: "1px solid var(--ink-04)",
        }}
      >
        <h1
          className="text-[22px] font-extrabold"
          style={{ color: "var(--ink)", letterSpacing: "-0.8px" }}
        >
          Đang theo dõi
        </h1>
        <p className="text-[13px] mt-0.5" style={{ color: "var(--ink-50)" }}>
          {total} căn đang trong quá trình
        </p>
      </div>

      {/* Scrollable content */}
      <div
        className="flex-1 overflow-y-auto px-5 py-4 space-y-6"
        style={{ paddingBottom: 32 }}
      >
        {total === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <p className="text-[15px] font-medium" style={{ color: "var(--ink-30)" }}>
              Chưa có căn nào đang theo dõi
            </p>
            <p className="text-[13px] mt-1" style={{ color: "var(--ink-30)" }}>
              Vuốt phải để thêm căn vào danh sách
            </p>
          </div>
        ) : (
          <>
            <TrackSection
              title="Đang xem xét"
              dotColor="var(--amber)"
              listings={researching}
              campaignId={campaignId}
            />
            <TrackSection
              title="Đã liên hệ"
              dotColor="var(--terra)"
              listings={contacted}
              campaignId={campaignId}
            />
            <TrackSection
              title="Xong"
              dotColor="var(--ink-30)"
              listings={done}
              collapsedByDefault={done.length > 0}
              campaignId={campaignId}
            />
          </>
        )}
      </div>
    </div>
  );
}
