"use client";

import { useState, useMemo } from "react";
import { ArrowLeft, Plus, X, Globe } from "lucide-react";
import type { CampaignPreferences } from "@/types";

interface SourcesStepProps {
  preferences: CampaignPreferences;
  onConfirm: (sources: string[]) => void;
  onBack: () => void;
}

interface Source {
  url: string;
  label: string;
  platform: "nhatot" | "bds" | "facebook" | "zalo" | "custom";
  enabled: boolean;
}

const DEFAULT_SOURCES: Source[] = [
  {
    url: "https://www.nhatot.com/thue-phong-tro",
    label: "Nhà Tốt",
    platform: "nhatot",
    enabled: true,
  },
  {
    url: "https://batdongsan.com.vn/cho-thue",
    label: "Batdongsan.com.vn",
    platform: "bds",
    enabled: true,
  },
];

const DISTRICT_GROUPS: Record<string, { url: string; label: string }[]> = {
  "Bình Thạnh": [
    {
      url: "https://facebook.com/groups/phongtrobinhthanh",
      label: "Phòng Trọ Bình Thạnh",
    },
  ],
  "Quận 7": [
    {
      url: "https://facebook.com/groups/phongtroquan7",
      label: "Phòng Trọ Quận 7",
    },
  ],
  "Quận 1": [
    {
      url: "https://facebook.com/groups/phongtroquan1hcm",
      label: "Phòng Trọ Quận 1",
    },
  ],
  "Phú Nhuận": [
    {
      url: "https://facebook.com/groups/phongtrophunhuan",
      label: "Phòng Trọ Phú Nhuận",
    },
  ],
  "Tân Bình": [
    {
      url: "https://facebook.com/groups/phongtrotanbinh",
      label: "Phòng Trọ Tân Bình",
    },
  ],
  "Gò Vấp": [
    {
      url: "https://facebook.com/groups/phongtrogovap",
      label: "Phòng Trọ Gò Vấp",
    },
  ],
};

const PLATFORM_COLORS: Record<string, string> = {
  nhatot: "#F57C00",
  bds: "#1976D2",
  facebook: "#1877F2",
  zalo: "#0068FF",
  custom: "var(--ink-30)",
};

const PLATFORM_ICONS: Record<string, string> = {
  nhatot: "🏠",
  bds: "🏢",
  facebook: "📘",
  zalo: "💬",
  custom: "🔗",
};

function getPlatformFromUrl(url: string): Source["platform"] {
  if (url.includes("nhatot.com")) return "nhatot";
  if (url.includes("batdongsan.com")) return "bds";
  if (url.includes("facebook.com")) return "facebook";
  if (url.includes("zalo")) return "zalo";
  return "custom";
}

function Toggle({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: () => void;
}) {
  return (
    <button
      onClick={onChange}
      className="w-[44px] h-[26px] rounded-full p-[3px] transition-colors"
      style={{
        background: enabled ? "var(--terra)" : "var(--ink-15)",
      }}
    >
      <div
        className="w-5 h-5 rounded-full transition-transform"
        style={{
          background: "white",
          transform: enabled ? "translateX(18px)" : "translateX(0)",
        }}
      />
    </button>
  );
}

export function SourcesStep({ preferences, onConfirm, onBack }: SourcesStepProps) {
  const [defaults, setDefaults] = useState<Source[]>(DEFAULT_SOURCES);
  const [customSources, setCustomSources] = useState<Source[]>([]);
  const [urlInput, setUrlInput] = useState("");
  const [error, setError] = useState("");

  const districtSuggestions = useMemo(() => {
    if (!preferences.district) return [];

    const suggestions: Source[] = [];
    const districts = preferences.district.split(/[,，、]/);

    for (const district of districts) {
      const trimmed = district.trim();
      const groups = DISTRICT_GROUPS[trimmed];
      if (groups) {
        for (const group of groups) {
          if (!suggestions.find((s) => s.url === group.url)) {
            suggestions.push({
              ...group,
              platform: "facebook",
              enabled: true,
            });
          }
        }
      }
    }

    return suggestions;
  }, [preferences.district]);

  const [districtSources, setDistrictSources] =
    useState<Source[]>(districtSuggestions);

  const toggleDefault = (index: number) => {
    setDefaults((prev) =>
      prev.map((s, i) => (i === index ? { ...s, enabled: !s.enabled } : s))
    );
  };

  const toggleDistrict = (index: number) => {
    setDistrictSources((prev) =>
      prev.map((s, i) => (i === index ? { ...s, enabled: !s.enabled } : s))
    );
  };

  const addCustomUrl = () => {
    const url = urlInput.trim();
    if (!url) return;

    if (!url.startsWith("http")) {
      setError("Link không hợp lệ");
      return;
    }
    if (
      customSources.find((s) => s.url === url) ||
      defaults.find((s) => s.url === url)
    ) {
      setError("Link này đã được thêm");
      return;
    }

    const platform = getPlatformFromUrl(url);
    const label = new URL(url).hostname.replace("www.", "");

    setCustomSources((prev) => [
      ...prev,
      { url, label, platform, enabled: true },
    ]);
    setUrlInput("");
    setError("");
  };

  const removeCustom = (url: string) => {
    setCustomSources((prev) => prev.filter((s) => s.url !== url));
  };

  const handleConfirm = () => {
    const sources = [
      ...defaults.filter((s) => s.enabled).map((s) => s.url),
      ...districtSources.filter((s) => s.enabled).map((s) => s.url),
      ...customSources.filter((s) => s.enabled).map((s) => s.url),
    ];
    onConfirm(sources);
  };

  const totalSources =
    defaults.filter((s) => s.enabled).length +
    districtSources.filter((s) => s.enabled).length +
    customSources.filter((s) => s.enabled).length;

  return (
    <div
      className="flex flex-col min-h-screen"
      style={{ background: "var(--cream)" }}
    >
      {/* Header */}
      <div className="flex-shrink-0 pt-[60px] px-5 pb-6">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-[13px] font-medium mb-4 -ml-1"
          style={{ color: "var(--ink-50)" }}
        >
          <ArrowLeft size={16} />
          Quay lại
        </button>
        <h1
          className="text-[22px] font-extrabold tracking-tight"
          style={{ color: "var(--ink)" }}
        >
          Chọn nguồn tìm kiếm
        </h1>
        <p
          className="text-[13px] font-medium mt-1"
          style={{ color: "var(--ink-50)" }}
        >
          Mình sẽ quét các trang này để tìm phòng cho bạn
        </p>
      </div>

      {/* Content */}
      <div className="flex-1 px-5 overflow-y-auto pb-4">
        {/* Default sources */}
        <div className="mb-6">
          <p
            className="text-[11px] font-semibold uppercase tracking-wide mb-3"
            style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
          >
            Nguồn đề xuất
          </p>
          <div className="flex flex-col gap-2">
            {defaults.map((source, i) => (
              <SourceCard
                key={source.url}
                source={source}
                onToggle={() => toggleDefault(i)}
              />
            ))}
          </div>
        </div>

        {/* District suggestions */}
        {districtSources.length > 0 && (
          <div className="mb-6">
            <p
              className="text-[11px] font-semibold uppercase tracking-wide mb-1"
              style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
            >
              Đề xuất theo khu vực
            </p>
            <p
              className="text-[12px] mb-3"
              style={{ color: "var(--ink-30)" }}
            >
              Dựa trên khu vực bạn chọn: {preferences.district}
            </p>
            <div className="flex flex-col gap-2">
              {districtSources.map((source, i) => (
                <SourceCard
                  key={source.url}
                  source={source}
                  onToggle={() => toggleDistrict(i)}
                />
              ))}
            </div>
          </div>
        )}

        {/* Custom sources */}
        <div>
          <p
            className="text-[11px] font-semibold uppercase tracking-wide mb-3"
            style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
          >
            Thêm nguồn khác
          </p>

          {customSources.length > 0 && (
            <div className="flex flex-col gap-2 mb-3">
              {customSources.map((source) => (
                <div
                  key={source.url}
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
                    onClick={() => removeCustom(source.url)}
                    className="w-8 h-8 rounded-full flex items-center justify-center"
                    style={{ background: "var(--ink-08)" }}
                  >
                    <X size={16} style={{ color: "var(--ink-50)" }} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <div
            className="flex items-center gap-2 p-3"
            style={{
              background: "var(--ds-white)",
              border: "1px solid var(--ink-15)",
              borderRadius: "var(--r-lg)",
            }}
          >
            <Globe size={18} style={{ color: "var(--ink-30)" }} />
            <input
              value={urlInput}
              onChange={(e) => {
                setUrlInput(e.target.value);
                setError("");
              }}
              onKeyDown={(e) =>
                e.key === "Enter" && (e.preventDefault(), addCustomUrl())
              }
              placeholder="Dán link nhóm Facebook, Zalo..."
              className="flex-1 bg-transparent outline-none text-[13px] font-medium"
              style={{ color: "var(--ink)" }}
            />
            <button
              onClick={addCustomUrl}
              disabled={!urlInput.trim()}
              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors"
              style={{
                background: urlInput.trim() ? "var(--terra)" : "var(--ink-08)",
              }}
            >
              <Plus
                size={20}
                style={{ color: urlInput.trim() ? "white" : "var(--ink-30)" }}
              />
            </button>
          </div>
          {error && (
            <p className="text-[12px] mt-2" style={{ color: "#C03" }}>
              {error}
            </p>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="flex-shrink-0 px-5 pt-4 pb-8">
        <button
          onClick={handleConfirm}
          disabled={totalSources === 0}
          className="w-full h-[52px] text-[15px] font-semibold transition-colors"
          style={{
            background: totalSources > 0 ? "var(--terra)" : "var(--ink-15)",
            color: totalSources > 0 ? "white" : "var(--ink-30)",
            borderRadius: "var(--r-lg)",
          }}
        >
          Tiếp tục → ({totalSources} nguồn)
        </button>
      </div>
    </div>
  );
}

function SourceCard({
  source,
  onToggle,
}: {
  source: Source;
  onToggle: () => void;
}) {
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
      <Toggle enabled={source.enabled} onChange={onToggle} />
    </div>
  );
}
