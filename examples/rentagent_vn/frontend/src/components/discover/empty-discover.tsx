"use client";

import { Search } from "lucide-react";

export function EmptyDiscover() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-8 text-center">
      <div
        className="w-16 h-16 rounded-full flex items-center justify-center mb-4"
        style={{ background: "var(--ink-04)" }}
      >
        <Search size={28} style={{ color: "var(--ink-30)" }} />
      </div>
      <h2
        className="text-lg font-bold mb-2"
        style={{ color: "var(--ink)", letterSpacing: "-0.5px" }}
      >
        Đã xem hết rồi!
      </h2>
      <p className="text-[13px]" style={{ color: "var(--ink-50)" }}>
        Bot sẽ quét thêm tin mới cho bạn. Bạn sẽ nhận thông báo khi có tin mới.
      </p>
    </div>
  );
}
