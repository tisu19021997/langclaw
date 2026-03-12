"use client";

import { X } from "lucide-react";
import { SettingsToggle } from "./settings-toggle";

export type SourcePlatform = "nhatot" | "bds" | "facebook" | "zalo" | "thuephongtro" | "phongtot" | "tromoi" | "lozido" | "custom";

export interface Source {
  url: string;
  label: string;
  platform: SourcePlatform;
  enabled: boolean;
}

export const PLATFORM_COLORS: Record<SourcePlatform, string> = {
  nhatot: "#F57C00",
  bds: "#1976D2",
  facebook: "#1877F2",
  zalo: "#0068FF",
  thuephongtro: "#E91E63",
  phongtot: "#4CAF50",
  tromoi: "#9C27B0",
  lozido: "#FF5722",
  custom: "var(--ink-30)",
};

export const PLATFORM_ICONS: Record<SourcePlatform, string> = {
  nhatot: "🏠",
  bds: "🏢",
  facebook: "📘",
  zalo: "💬",
  thuephongtro: "🏡",
  phongtot: "✨",
  tromoi: "🆕",
  lozido: "📍",
  custom: "🔗",
};

export const DEFAULT_SOURCES: Omit<Source, "enabled">[] = [
  { url: "https://www.nhatot.com/thue-phong-tro", label: "Nhà Tốt", platform: "nhatot" },
  { url: "https://batdongsan.com.vn/cho-thue", label: "Batdongsan.com.vn", platform: "bds" },
  { url: "https://thuephongtro.com/", label: "Thuê Phòng Trọ", platform: "thuephongtro" },
  { url: "https://phongtot.com/", label: "Phòng Tốt", platform: "phongtot" },
  { url: "https://tromoi.com/", label: "Trọ Mới", platform: "tromoi" },
  { url: "https://lozido.com/", label: "LOZIDO", platform: "lozido" },
];

export const DISTRICT_GROUPS: Record<string, { url: string; label: string }[]> = {
  "Bình Thạnh": [
    { url: "https://facebook.com/groups/phongtrobinhthanh", label: "Phòng Trọ Bình Thạnh" },
  ],
  "Quận 7": [
    { url: "https://facebook.com/groups/phongtroquan7", label: "Phòng Trọ Quận 7" },
  ],
  "Quận 1": [
    { url: "https://facebook.com/groups/phongtroquan1hcm", label: "Phòng Trọ Quận 1" },
  ],
  "Phú Nhuận": [
    { url: "https://facebook.com/groups/phongtrophunhuan", label: "Phòng Trọ Phú Nhuận" },
  ],
  "Tân Bình": [
    { url: "https://facebook.com/groups/phongtrotanbinh", label: "Phòng Trọ Tân Bình" },
  ],
  "Gò Vấp": [
    { url: "https://facebook.com/groups/phongtrogovap", label: "Phòng Trọ Gò Vấp" },
  ],
};

export function getPlatformFromUrl(url: string): SourcePlatform {
  if (url.includes("nhatot.com")) return "nhatot";
  if (url.includes("batdongsan.com")) return "bds";
  if (url.includes("facebook.com")) return "facebook";
  if (url.includes("zalo")) return "zalo";
  if (url.includes("thuephongtro.com")) return "thuephongtro";
  if (url.includes("phongtot.com")) return "phongtot";
  if (url.includes("tromoi.com")) return "tromoi";
  if (url.includes("lozido.com")) return "lozido";
  return "custom";
}

interface SourceCardProps {
  source: Source;
  onToggle: () => void;
}

export function SourceCard({ source, onToggle }: SourceCardProps) {
  return (
    <div
      className="flex items-center gap-3 p-3 transition-opacity"
      style={{
        background: "var(--ds-white)",
        border: "1px solid var(--ink-08)",
        borderRadius: "var(--r-lg)",
        opacity: source.enabled ? 1 : 0.5,
      }}
    >
      <div
        className="w-9 h-9 rounded-[var(--r-sm)] flex items-center justify-center text-[16px]"
        style={{ background: `${PLATFORM_COLORS[source.platform]}20` }}
      >
        {PLATFORM_ICONS[source.platform]}
      </div>
      <div className="flex-1 min-w-0">
        <p
          className="text-[13px] font-semibold truncate"
          style={{ color: "var(--ink)" }}
        >
          {source.label}
        </p>
        <p
          className="text-[12px] truncate"
          style={{ color: "var(--ink-30)" }}
        >
          {source.url}
        </p>
      </div>
      <SettingsToggle enabled={source.enabled} onChange={onToggle} on={source.enabled} />
    </div>
  );
}

interface CustomSourceCardProps {
  source: Source;
  onRemove: () => void;
}

export function CustomSourceCard({ source, onRemove }: CustomSourceCardProps) {
  return (
    <div
      className="flex items-center gap-3 p-3"
      style={{
        background: "var(--ds-white)",
        border: "1px solid var(--ink-08)",
        borderRadius: "var(--r-lg)",
      }}
    >
      <div
        className="w-9 h-9 rounded-[var(--r-sm)] flex items-center justify-center text-[16px]"
        style={{ background: `${PLATFORM_COLORS[source.platform]}20` }}
      >
        {PLATFORM_ICONS[source.platform]}
      </div>
      <div className="flex-1 min-w-0">
        <p
          className="text-[13px] font-semibold truncate"
          style={{ color: "var(--ink)" }}
        >
          {source.label}
        </p>
        <p
          className="text-[12px] truncate"
          style={{ color: "var(--ink-30)" }}
        >
          {source.url}
        </p>
      </div>
      <button
        onClick={onRemove}
        className="w-8 h-8 rounded-full flex items-center justify-center"
        style={{ background: "var(--ink-08)" }}
      >
        <X size={16} style={{ color: "var(--ink-50)" }} />
      </button>
    </div>
  );
}

interface SourceIconProps {
  platform: SourcePlatform;
  size?: number;
}

export function SourceIcon({ platform, size = 36 }: SourceIconProps) {
  return (
    <div
      className="flex items-center justify-center rounded-full"
      style={{
        width: size,
        height: size,
        background: `${PLATFORM_COLORS[platform]}20`,
        fontSize: size * 0.44,
      }}
    >
      {PLATFORM_ICONS[platform]}
    </div>
  );
}
