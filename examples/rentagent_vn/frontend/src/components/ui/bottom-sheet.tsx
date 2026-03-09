"use client";

import { X } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  maxHeight?: string;
  children: React.ReactNode;
}

export function BottomSheet({
  open,
  onClose,
  title,
  subtitle,
  maxHeight = "85vh",
  children,
}: BottomSheetProps) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent
        side="bottom"
        showCloseButton={false}
        className="p-0 flex flex-col gap-0 outline-none"
        style={{
          borderRadius: "24px 24px 0 0",
          maxHeight,
          overflow: "hidden",
        }}
      >
        {/* Drag handle */}
        <div className="flex justify-center pt-3 pb-1 flex-shrink-0">
          <div
            className="rounded-full"
            style={{ width: 32, height: 4, background: "var(--ink-15)" }}
          />
        </div>

        {/* Header */}
        <SheetHeader className="flex-shrink-0 px-5 py-3 flex-row items-start justify-between gap-3">
          <div className="flex flex-col gap-0.5 min-w-0">
            <SheetTitle
              className="text-left text-[16px] font-bold leading-tight truncate"
              style={{ color: "var(--ink)" }}
            >
              {title}
            </SheetTitle>
            {subtitle && (
              <p className="text-[12px]" style={{ color: "var(--ink-50)" }}>
                {subtitle}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="flex-shrink-0 flex items-center justify-center rounded-full mt-0.5"
            style={{
              width: 28,
              height: 28,
              background: "var(--ink-08)",
              color: "var(--ink-50)",
            }}
          >
            <X size={14} />
          </button>
        </SheetHeader>

        {/* Content */}
        <div className="flex-1 overflow-hidden flex flex-col min-h-0">
          {children}
        </div>
      </SheetContent>
    </Sheet>
  );
}
