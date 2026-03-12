"use client";

import { useState } from "react";
import { ArrowLeft, Pencil } from "lucide-react";
import type { CampaignPreferences } from "@/types";

interface ConfirmStepProps {
  preferences: CampaignPreferences;
  onConfirm: (prefs: CampaignPreferences) => void;
  onBack: () => void;
}

const PREF_FIELDS: {
  key: keyof CampaignPreferences;
  label: string;
  placeholder: string;
  type?: string;
}[] = [
  { key: "district", label: "Khu vực", placeholder: "Quận 7, Bình Thạnh..." },
  {
    key: "property_type",
    label: "Loại hình",
    placeholder: "Phòng trọ, căn hộ mini...",
  },
  {
    key: "bedrooms",
    label: "Phòng ngủ",
    placeholder: "1, 2, 3...",
    type: "number",
  },
  {
    key: "min_price",
    label: "Giá tối thiểu",
    placeholder: "5,000,000",
    type: "number",
  },
  {
    key: "max_price",
    label: "Giá tối đa",
    placeholder: "10,000,000",
    type: "number",
  },
  {
    key: "min_area",
    label: "Diện tích tối thiểu",
    placeholder: "25 m²",
    type: "number",
  },
  {
    key: "notes",
    label: "Yêu cầu khác",
    placeholder: "Có ban công, gần metro...",
  },
];

function formatValue(key: string, value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  if (key === "min_price" || key === "max_price") {
    return `${Number(value).toLocaleString("vi-VN")}đ`;
  }
  if (key === "min_area") {
    return `${value}m²`;
  }
  if (key === "bedrooms") {
    return `${value} PN`;
  }
  return String(value);
}

export function ConfirmStep({
  preferences,
  onConfirm,
  onBack,
}: ConfirmStepProps) {
  const [prefs, setPrefs] = useState<CampaignPreferences>({ ...preferences });
  const [editing, setEditing] = useState<string | null>(null);
  const [inputValues, setInputValues] = useState<Record<string, string>>(
    Object.fromEntries(
      PREF_FIELDS.map((f) => [f.key, String(prefs[f.key] ?? "")])
    )
  );

  const commitField = (key: string) => {
    const field = PREF_FIELDS.find((f) => f.key === key)!;
    const raw = inputValues[key];
    const value = field.type === "number" ? Number(raw) || "" : raw;
    setPrefs((prev) => ({ ...prev, [key]: value || undefined }));
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
    (f) => prefs[f.key] !== undefined && prefs[f.key] !== ""
  );
  const emptyFields = PREF_FIELDS.filter(
    (f) => prefs[f.key] === undefined || prefs[f.key] === ""
  );

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
          Xác nhận tiêu chí
        </h1>
        <p
          className="text-[13px] font-medium mt-1"
          style={{ color: "var(--ink-50)" }}
        >
          Chạm vào để chỉnh sửa
        </p>
      </div>

      {/* Tags */}
      <div className="flex-1 px-5 overflow-y-auto">
        <div className="flex flex-wrap gap-[10px]">
          {/* Filled fields as tag pills */}
          {filledFields.map((field) => (
            <button
              key={field.key}
              onClick={() => {
                setInputValues((prev) => ({
                  ...prev,
                  [field.key]: String(prefs[field.key] ?? ""),
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
                  {formatValue(field.key, prefs[field.key])}
                </span>
              </div>
              <Pencil size={14} style={{ color: "var(--ink-30)" }} />
            </button>
          ))}

          {/* Empty fields as add buttons */}
          {emptyFields.map((field) => (
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
              + Thêm {field.label.toLowerCase()}
            </button>
          ))}
        </div>

        {/* Edit field */}
        {editing && (
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

      {/* Footer */}
      <div className="flex-shrink-0 px-5 pt-4 pb-8">
        <button
          onClick={() => onConfirm(prefs)}
          className="w-full h-[52px] text-[15px] font-semibold transition-colors"
          style={{
            background: "var(--terra)",
            color: "white",
            borderRadius: "var(--r-lg)",
          }}
        >
          Tiếp tục →
        </button>
      </div>
    </div>
  );
}
