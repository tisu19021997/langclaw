"use client";

import { useState } from "react";
import { Pencil } from "lucide-react";
import type { CampaignPreferences } from "@/types";

export interface PreferenceField {
  key: keyof CampaignPreferences;
  label: string;
  placeholder: string;
  type?: string;
}

export const PREF_FIELDS: PreferenceField[] = [
  { key: "district", label: "Location", placeholder: "District 7, Binh Thanh..." },
  { key: "property_type", label: "Property type", placeholder: "Room, apartment, studio..." },
  { key: "bedrooms", label: "Bedrooms", placeholder: "1, 2, 3...", type: "number" },
  { key: "min_price", label: "Min price", placeholder: "5,000,000", type: "number" },
  { key: "max_price", label: "Max price", placeholder: "10,000,000", type: "number" },
  { key: "min_area", label: "Min area", placeholder: "25 m²", type: "number" },
  { key: "notes", label: "Other requirements", placeholder: "Has balcony, near metro..." },
];

export function formatPrefValue(key: string, value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  if (key === "min_price" || key === "max_price") {
    return `${Number(value).toLocaleString("vi-VN")} VND`;
  }
  if (key === "min_area") {
    return `${value}m²`;
  }
  if (key === "bedrooms") {
    return `${value} PN`;
  }
  return String(value);
}

interface PreferenceTagsProps {
  preferences: CampaignPreferences;
  onChange: (prefs: CampaignPreferences) => void;
  editable?: boolean;
}

export function PreferenceTags({
  preferences,
  onChange,
  editable = true,
}: PreferenceTagsProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [inputValues, setInputValues] = useState<Record<string, string>>(
    Object.fromEntries(
      PREF_FIELDS.map((f) => [f.key, String(preferences[f.key] ?? "")])
    )
  );

  const commitField = (key: string) => {
    const field = PREF_FIELDS.find((f) => f.key === key)!;
    const raw = inputValues[key];
    const value = field.type === "number" ? Number(raw) || "" : raw;
    onChange({ ...preferences, [key]: value || undefined });
    setEditing(null);
  };

  const handleKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement>,
    key: string
  ) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commitField(key);
    }
  };

  const filledFields = PREF_FIELDS.filter(
    (f) => preferences[f.key] !== undefined && preferences[f.key] !== ""
  );
  const emptyFields = PREF_FIELDS.filter(
    (f) => preferences[f.key] === undefined || preferences[f.key] === ""
  );

  return (
    <div>
      <div className="flex flex-wrap gap-[10px]">
        {/* Filled fields as tag pills */}
        {filledFields.map((field) => (
          <button
            key={field.key}
            onClick={() => {
              if (!editable) return;
              setInputValues((prev) => ({
                ...prev,
                [field.key]: String(preferences[field.key] ?? ""),
              }));
              setEditing(editing === field.key ? null : field.key);
            }}
            className="flex items-center gap-2 px-4 py-2 transition-all"
            style={{
              background:
                editing === field.key
                  ? "var(--terra-08)"
                  : "var(--ds-white)",
              border:
                editing === field.key
                  ? "2px solid var(--terra)"
                  : "1px solid var(--ink-08)",
              borderRadius: "var(--r-full)",
              cursor: editable ? "pointer" : "default",
            }}
          >
            <div className="flex flex-col items-start">
              <span
                className="text-[11px] font-semibold uppercase tracking-wide"
                style={{ color: "var(--ink-30)" }}
              >
                {field.label}
              </span>
              <span
                className="text-[14px] font-semibold"
                style={{ color: "var(--ink)" }}
              >
                {formatPrefValue(field.key, preferences[field.key])}
              </span>
            </div>
            {editable && <Pencil size={14} style={{ color: "var(--ink-30)" }} />}
          </button>
        ))}

        {/* Empty fields as add buttons */}
        {editable && emptyFields.map((field) => (
          <button
            key={field.key}
            onClick={() => {
              setInputValues((prev) => ({ ...prev, [field.key]: "" }));
              setEditing(field.key);
            }}
            className="px-4 py-2 text-[13px] font-medium transition-all"
            style={{
              background:
                editing === field.key
                  ? "var(--terra-08)"
                  : "transparent",
              border:
                editing === field.key
                  ? "2px solid var(--terra)"
                  : "1px dashed var(--ink-15)",
              borderRadius: "var(--r-full)",
              color: "var(--ink-30)",
            }}
          >
            + Add {field.label.toLowerCase()}
          </button>
        ))}
      </div>

      {/* Edit field */}
      {editing && editable && (
        <div className="mt-6">
          <label
            className="block text-[11px] font-semibold uppercase tracking-wide mb-2"
            style={{ color: "var(--ink-30)" }}
          >
            {PREF_FIELDS.find((f) => f.key === editing)?.label}
          </label>
          <input
            type={
              PREF_FIELDS.find((f) => f.key === editing)?.type || "text"
            }
            value={inputValues[editing] ?? ""}
            onChange={(e) =>
              setInputValues((prev) => ({
                ...prev,
                [editing]: e.target.value,
              }))
            }
            onKeyDown={(e) => handleKeyDown(e, editing)}
            onBlur={() => commitField(editing)}
            placeholder={PREF_FIELDS.find((f) => f.key === editing)?.placeholder}
            autoFocus
            className="w-full px-4 py-3 text-[14px] font-medium outline-none transition-colors"
            style={{
              background: "var(--ds-white)",
              border: "1px solid var(--ink-15)",
              borderRadius: "var(--r-sm)",
              color: "var(--ink)",
            }}
          />
        </div>
      )}
    </div>
  );
}

interface PreferencePillsReadOnlyProps {
  preferences: CampaignPreferences;
}

export function PreferencePillsReadOnly({ preferences }: PreferencePillsReadOnlyProps) {
  const pills: string[] = [];
  if (preferences?.district) pills.push(preferences.district);
  if (preferences?.bedrooms) pills.push(`${preferences.bedrooms}PN`);
  if (preferences?.max_price)
    pills.push(`≤ ${Math.round(preferences.max_price / 1_000_000)}M`);
  if (preferences?.min_price && preferences?.max_price)
    pills[pills.length - 1] = `${Math.round(preferences.min_price / 1_000_000)}–${Math.round(preferences.max_price / 1_000_000)}M`;
  if (preferences?.min_area) pills.push(`≥ ${preferences.min_area}m²`);

  if (pills.length === 0) {
    return (
      <span className="text-[13px]" style={{ color: "var(--ink-30)" }}>
        No search criteria set
      </span>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {pills.map((pill) => (
        <span
          key={pill}
          className="px-3 py-1 text-[12px] font-semibold"
          style={{
            background: "var(--terra-08)",
            color: "var(--terra)",
            borderRadius: "var(--r-full)",
          }}
        >
          {pill}
        </span>
      ))}
    </div>
  );
}
