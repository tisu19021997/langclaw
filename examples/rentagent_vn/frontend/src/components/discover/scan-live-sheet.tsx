"use client";

import { useScanStreamStore } from "@/stores/scan-stream-store";
import { BottomSheet } from "@/components/ui/bottom-sheet";

interface ScanLiveSheetProps {
  open: boolean;
  onClose: () => void;
}

function getTabLabel(url: string): string {
  try {
    return new URL(url).hostname.replace("www.", "");
  } catch {
    return url.slice(0, 20);
  }
}

export function ScanLiveSheet({ open, onClose }: ScanLiveSheetProps) {
  const {
    activeUrl,
    streamingUrls,
    completedUrls,
    totalUrls,
    listingsFound,
    setActiveUrl,
  } = useScanStreamStore();

  const iframeUrl = activeUrl ? streamingUrls[activeUrl] : null;
  const urlKeys = Object.keys(streamingUrls);
  const hasMultipleTabs = urlKeys.length > 1;

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title="Finding apartments..."
      subtitle="Bot is browsing the web to find matching listings"
      maxHeight="95vh"
    >
      {/* Tab bar for multiple URLs */}
      {hasMultipleTabs && (
        <div
          className="flex-shrink-0 flex gap-1.5 px-4 py-2 overflow-x-auto"
          style={{ borderBottom: "1px solid var(--ink-04)" }}
        >
          {urlKeys.map((url) => (
            <button
              key={url}
              onClick={() => setActiveUrl(url)}
              className="px-3 py-1.5 rounded-full text-[12px] whitespace-nowrap transition-colors"
              style={{
                background:
                  url === activeUrl ? "var(--amber)" : "var(--ink-08)",
                color: url === activeUrl ? "white" : "var(--ink-70)",
                fontWeight: url === activeUrl ? 600 : 400,
              }}
            >
              {getTabLabel(url)}
            </button>
          ))}
        </div>
      )}

      {/* iFrame area — scaled up for better visibility */}
      <div
        className="flex-1 relative min-h-0 overflow-hidden"
        style={{ minHeight: "60vh" }}
      >
        {iframeUrl ? (
          <iframe
            src={iframeUrl}
            className="absolute inset-0 border-0"
            style={{
              width: "100%",
              height: "100%",
              transform: "scale(1.5)",
              transformOrigin: "top center",
            }}
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
                Starting up...
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
          {completedUrls}/{totalUrls || "..."} pages
        </span>
        <span
          className="text-[12px] font-semibold"
          style={{ color: "var(--terra)" }}
        >
          {listingsFound} listings found
        </span>
      </div>
    </BottomSheet>
  );
}
