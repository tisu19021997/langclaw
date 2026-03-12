"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}

export function BottomSheet({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
}: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && open) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 transition-opacity"
        onClick={onClose}
      />

      {/* Sheet */}
      <div
        ref={sheetRef}
        className="absolute bottom-0 left-0 right-0 flex flex-col animate-slide-up"
        style={{
          maxHeight: "90vh",
          background: "var(--cream)",
          borderTopLeftRadius: "var(--r-xl)",
          borderTopRightRadius: "var(--r-xl)",
        }}
      >
        {/* Drag handle */}
        <div className="flex justify-center pt-3 pb-2">
          <div
            className="w-10 h-1 rounded-full"
            style={{ background: "var(--ink-15)" }}
          />
        </div>

        {/* Header */}
        <div className="flex items-start justify-between px-5 pb-4">
          <div className="flex-1">
            <h2
              className="text-[22px] font-extrabold"
              style={{ color: "var(--ink)", letterSpacing: "-0.8px" }}
            >
              {title}
            </h2>
            {subtitle && (
              <p
                className="text-[13px] font-medium mt-1"
                style={{ color: "var(--ink-50)" }}
              >
                {subtitle}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="flex items-center justify-center w-8 h-8 rounded-full -mr-1"
            style={{ background: "var(--ink-08)" }}
          >
            <X size={18} style={{ color: "var(--ink-50)" }} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5">{children}</div>

        {/* Footer */}
        {footer && (
          <div className="flex-shrink-0 px-5 pt-4 pb-8">{footer}</div>
        )}
      </div>

      <style jsx global>{`
        @keyframes slide-up {
          from {
            transform: translateY(100%);
          }
          to {
            transform: translateY(0);
          }
        }
        .animate-slide-up {
          animation: slide-up 0.3s ease-out;
        }
      `}</style>
    </div>
  );
}
