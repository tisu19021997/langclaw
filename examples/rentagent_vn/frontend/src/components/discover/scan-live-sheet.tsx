"use client";

import { useScanStreamStore } from "@/stores/scan-stream-store";
import { BottomSheet } from "@/components/ui/bottom-sheet";

interface ScanLiveSheetProps {
  open: boolean;
  onClose: () => void;
}

export function ScanLiveSheet({ open, onClose }: ScanLiveSheetProps) {
  const { activeUrl, streamingUrls, completedUrls, totalUrls, listingsFound } =
    useScanStreamStore();

  const iframeUrl = activeUrl ? streamingUrls[activeUrl] : null;

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title="Đang tìm căn hộ..."
      subtitle="Bot đang duyệt web để tìm tin phù hợp"
      maxHeight="85vh"
    >
      {/* iFrame area */}
      <div className="flex-1 relative min-h-0" style={{ minHeight: 300 }}>
        {iframeUrl ? (
          <iframe
            src={iframeUrl}
            className="w-full h-full border-0"
            sandbox="allow-scripts allow-same-origin"
            title="Scan live preview"
          />
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{ background: "var(--cream-100)" }}
          >
            <div className="text-center space-y-2">
              <div
                className="w-8 h-8 border-2 border-t-transparent rounded-full animate-spin mx-auto"
                style={{ borderColor: "var(--amber)", borderTopColor: "transparent" }}
              />
              <p className="text-[13px]" style={{ color: "var(--ink-50)" }}>
                Đang khởi động...
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div
        className="flex-shrink-0 flex items-center justify-between px-5 py-3"
        style={{ borderTop: "1px solid var(--ink-04)" }}
      >
        <span className="text-[12px]" style={{ color: "var(--ink-50)" }}>
          {completedUrls}/{totalUrls || "..."} trang
        </span>
        <span
          className="text-[12px] font-semibold"
          style={{ color: "var(--terra)" }}
        >
          {listingsFound} tin tìm thấy
        </span>
      </div>
    </BottomSheet>
  );
}
