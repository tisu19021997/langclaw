"use client";

import { useState, useEffect, useMemo } from "react";
import { useCampaignStore } from "@/stores/campaign-store";
import { BottomSheet, PreferenceTags } from "@/components/shared";
import type { CampaignPreferences } from "@/types";
import * as api from "@/lib/api";

interface SearchPreferencesSheetProps {
  campaignId: string;
  open: boolean;
  onClose: () => void;
}

function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(handler);
    };
  }, [value, delay]);

  return debouncedValue;
}

export function SearchPreferencesSheet({
  campaignId,
  open,
  onClose,
}: SearchPreferencesSheetProps) {
  const campaign = useCampaignStore((s) => s.campaign);
  const updateCampaign = useCampaignStore((s) => s.updateCampaign);
  const originalPrefs = campaign?.preferences ?? {};

  const [prefs, setPrefs] = useState<CampaignPreferences>({ ...originalPrefs });
  const [saving, setSaving] = useState(false);
  const [matchCount, setMatchCount] = useState<number | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);

  const debouncedPrefs = useDebounce(prefs, 500);

  const hasChanges = useMemo(() => {
    const keys: (keyof CampaignPreferences)[] = [
      "district",
      "property_type",
      "bedrooms",
      "min_price",
      "max_price",
      "min_area",
      "notes",
    ];
    return keys.some((key) => prefs[key] !== originalPrefs[key]);
  }, [prefs, originalPrefs]);

  useEffect(() => {
    if (open) {
      setPrefs({ ...originalPrefs });
      setMatchCount(null);
    }
  }, [open, originalPrefs]);

  useEffect(() => {
    if (!open || !hasChanges) return;

    const fetchPreview = async () => {
      setLoadingPreview(true);
      try {
        const result = await api.previewPreferences(campaignId, debouncedPrefs);
        setMatchCount(result.matching_count);
      } catch {
        // API may not exist yet, gracefully degrade
        setMatchCount(null);
      } finally {
        setLoadingPreview(false);
      }
    };

    fetchPreview();
  }, [debouncedPrefs, campaignId, open, hasChanges]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateCampaign(campaignId, { preferences: prefs });
      onClose();
    } catch (error) {
      console.error("Failed to save preferences:", error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title="Search Preferences"
      subtitle="Tap to edit"
      footer={
        <div className="space-y-3">
          {matchCount !== null && (
            <p
              className="text-[13px] text-center"
              style={{ color: "var(--ink-50)" }}
            >
              {loadingPreview ? (
                "Checking..."
              ) : (
                <>
                  With new criteria:{" "}
                  <span style={{ color: "var(--ink)", fontWeight: 600 }}>
                    {matchCount} listings
                  </span>{" "}
                  still match
                </>
              )}
            </p>
          )}
          <button
            onClick={handleSave}
            disabled={!hasChanges || saving}
            className="w-full h-[52px] text-[15px] font-semibold transition-colors"
            style={{
              background: hasChanges && !saving ? "var(--terra)" : "var(--ink-15)",
              color: hasChanges && !saving ? "white" : "var(--ink-30)",
              borderRadius: "var(--r-lg)",
            }}
          >
            {saving ? "Saving..." : "Save Changes"}
          </button>
        </div>
      }
    >
      <div className="pb-4">
        <PreferenceTags
          preferences={prefs}
          onChange={setPrefs}
          editable={true}
        />
      </div>
    </BottomSheet>
  );
}
