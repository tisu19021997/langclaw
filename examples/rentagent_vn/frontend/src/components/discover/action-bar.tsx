"use client";

import { X, Heart, Zap } from "lucide-react";

interface ActionBarProps {
  onSkip: () => void;
  onLike: () => void;
  onContact: () => void;
}

export function ActionBar({ onSkip, onLike, onContact }: ActionBarProps) {
  return (
    <div
      className="flex-shrink-0 flex items-center justify-center gap-5"
      style={{ padding: "16px 32px 20px" }}
    >
      {/* Skip */}
      <div className="flex flex-col items-center gap-1.5">
        <button
          onClick={onSkip}
          className="flex items-center justify-center rounded-full transition-transform active:scale-95"
          style={{
            width: 54,
            height: 54,
            background: "var(--ds-white)",
            border: "1px solid var(--ink-15)",
            boxShadow: "var(--shadow-card)",
          }}
        >
          <X size={22} style={{ color: "var(--ink-50)" }} />
        </button>
        <span className="text-[11px] font-medium" style={{ color: "var(--ink-30)" }}>
          Bỏ qua
        </span>
      </div>

      {/* Like / Xem thêm */}
      <div className="flex flex-col items-center gap-1.5">
        <button
          onClick={onLike}
          className="flex items-center justify-center rounded-full transition-transform active:scale-95"
          style={{
            width: 64,
            height: 64,
            background: "var(--terra)",
            boxShadow: "var(--shadow-float)",
          }}
        >
          <Heart size={26} fill="white" color="white" />
        </button>
        <span className="text-[11px] font-medium" style={{ color: "var(--terra)" }}>
          Xem thêm
        </span>
      </div>

      {/* Contact / Liên hệ luôn */}
      <div className="flex flex-col items-center gap-1.5">
        <button
          onClick={onContact}
          className="flex items-center justify-center rounded-full transition-transform active:scale-95"
          style={{
            width: 46,
            height: 46,
            background: "var(--ds-white)",
            border: "1px solid var(--ink-15)",
            boxShadow: "var(--shadow-card)",
          }}
        >
          <Zap size={18} style={{ color: "var(--ink-50)" }} />
        </button>
        <span className="text-[11px] font-medium" style={{ color: "var(--ink-30)" }}>
          Liên hệ luôn
        </span>
      </div>
    </div>
  );
}
