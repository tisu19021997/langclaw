"use client";

import { useState, useMemo, useEffect } from "react";
import { Plus, Globe } from "lucide-react";
import { useCampaignStore } from "@/stores/campaign-store";
import {
  BottomSheet,
  SourceCard,
  CustomSourceCard,
  DEFAULT_SOURCES,
  DISTRICT_GROUPS,
  getPlatformFromUrl,
  type Source,
} from "@/components/shared";

interface SourcesSheetProps {
  campaignId: string;
  open: boolean;
  onClose: () => void;
}

export function SourcesSheet({ campaignId, open, onClose }: SourcesSheetProps) {
  const campaign = useCampaignStore((s) => s.campaign);
  const updateCampaign = useCampaignStore((s) => s.updateCampaign);
  const currentSources = campaign?.sources ?? [];
  const preferences = campaign?.preferences ?? {};

  const [defaults, setDefaults] = useState<Source[]>([]);
  const [districtSources, setDistrictSources] = useState<Source[]>([]);
  const [customSources, setCustomSources] = useState<Source[]>([]);
  const [urlInput, setUrlInput] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;

    const defaultUrls = DEFAULT_SOURCES.map((s) => s.url);
    const districtUrls: string[] = [];

    if (preferences.district) {
      const districts = preferences.district.split(/[,，、]/);
      for (const district of districts) {
        const trimmed = district.trim();
        const groups = DISTRICT_GROUPS[trimmed];
        if (groups) {
          for (const group of groups) {
            if (!districtUrls.includes(group.url)) {
              districtUrls.push(group.url);
            }
          }
        }
      }
    }

    const customUrls = currentSources.filter(
      (url) => !defaultUrls.includes(url) && !districtUrls.includes(url)
    );

    setDefaults(
      DEFAULT_SOURCES.map((s) => ({
        ...s,
        enabled: currentSources.includes(s.url),
      }))
    );

    setDistrictSources(
      districtUrls.map((url) => {
        const group = Object.values(DISTRICT_GROUPS)
          .flat()
          .find((g) => g.url === url);
        return {
          url,
          label: group?.label ?? new URL(url).hostname,
          platform: getPlatformFromUrl(url),
          enabled: currentSources.includes(url),
        };
      })
    );

    setCustomSources(
      customUrls.map((url) => ({
        url,
        label: new URL(url).hostname.replace("www.", ""),
        platform: getPlatformFromUrl(url),
        enabled: true,
      }))
    );

    setUrlInput("");
    setError("");
  }, [open, currentSources, preferences.district]);

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
      setError("Invalid URL");
      return;
    }

    const allUrls = [
      ...defaults.map((s) => s.url),
      ...districtSources.map((s) => s.url),
      ...customSources.map((s) => s.url),
    ];

    if (allUrls.includes(url)) {
      setError("This URL is already added");
      return;
    }

    try {
      const platform = getPlatformFromUrl(url);
      const label = new URL(url).hostname.replace("www.", "");

      setCustomSources((prev) => [
        ...prev,
        { url, label, platform, enabled: true },
      ]);
      setUrlInput("");
      setError("");
    } catch {
      setError("Invalid URL");
    }
  };

  const removeCustom = (url: string) => {
    setCustomSources((prev) => prev.filter((s) => s.url !== url));
  };

  const totalSources =
    defaults.filter((s) => s.enabled).length +
    districtSources.filter((s) => s.enabled).length +
    customSources.filter((s) => s.enabled).length;

  const handleSave = async () => {
    setSaving(true);
    try {
      const sources = [
        ...defaults.filter((s) => s.enabled).map((s) => s.url),
        ...districtSources.filter((s) => s.enabled).map((s) => s.url),
        ...customSources.filter((s) => s.enabled).map((s) => s.url),
      ];
      await updateCampaign(campaignId, { sources });
      onClose();
    } catch (error) {
      console.error("Failed to save sources:", error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title="Search Sources"
      subtitle="Toggle sources to scan"
      footer={
        <button
          onClick={handleSave}
          disabled={totalSources === 0 || saving}
          className="w-full h-[52px] text-[15px] font-semibold transition-colors"
          style={{
            background: totalSources > 0 && !saving ? "var(--terra)" : "var(--ink-15)",
            color: totalSources > 0 && !saving ? "white" : "var(--ink-30)",
            borderRadius: "var(--r-lg)",
          }}
        >
          {saving ? "Saving..." : `Save (${totalSources} sources)`}
        </button>
      }
    >
      <div className="pb-4 space-y-6">
        {/* Default sources */}
        <div>
          <p
            className="text-[11px] font-semibold uppercase tracking-wide mb-3"
            style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
          >
            Popular Sources
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
          <div>
            <p
              className="text-[11px] font-semibold uppercase tracking-wide mb-1"
              style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
            >
              District Suggestions
            </p>
            <p
              className="text-[12px] mb-3"
              style={{ color: "var(--ink-30)" }}
            >
              Based on your area: {preferences.district}
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
            Add Custom Source
          </p>

          {customSources.length > 0 && (
            <div className="flex flex-col gap-2 mb-3">
              {customSources.map((source) => (
                <CustomSourceCard
                  key={source.url}
                  source={source}
                  onRemove={() => removeCustom(source.url)}
                />
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
              placeholder="Paste Facebook group or Zalo link..."
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
    </BottomSheet>
  );
}
