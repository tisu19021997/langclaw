"use client";

import { useState } from "react";
import { ArrowLeft } from "lucide-react";

interface FrequencyStepProps {
  onConfirm: (frequency: string) => void;
  onBack: () => void;
}

const FREQUENCIES = [
  {
    value: "manual",
    title: "Thủ công",
    description: "Quét khi bạn bấm nút. Phù hợp nếu không vội.",
  },
  {
    value: "1x_day",
    title: "Mỗi ngày",
    description: "Tự động quét lúc 8:00 sáng mỗi ngày.",
    recommended: true,
  },
  {
    value: "2x_day",
    title: "2 lần/ngày",
    description: "Quét lúc 8:00 sáng và 6:00 chiều. Không bỏ lỡ tin mới.",
  },
];

export function FrequencyStep({ onConfirm, onBack }: FrequencyStepProps) {
  const [selected, setSelected] = useState("1x_day");
  const [loading, setLoading] = useState(false);

  const handleConfirm = async () => {
    setLoading(true);
    await onConfirm(selected);
  };

  return (
    <div
      className="flex flex-col min-h-screen"
      style={{ background: "var(--cream)" }}
    >
      {/* Header */}
      <div className="flex-shrink-0 pt-[60px] px-5 pb-6">
        <button
          onClick={onBack}
          disabled={loading}
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
          Lịch quét tự động
        </h1>
        <p
          className="text-[13px] font-medium mt-1"
          style={{ color: "var(--ink-50)" }}
        >
          Bạn luôn có thể quét thủ công bất cứ lúc nào
        </p>
      </div>

      {/* Options */}
      <div className="flex-1 px-5">
        <div className="flex flex-col gap-3">
          {FREQUENCIES.map((freq) => {
            const isSelected = selected === freq.value;

            return (
              <button
                key={freq.value}
                onClick={() => setSelected(freq.value)}
                disabled={loading}
                className="flex items-start gap-3 p-4 text-left transition-all"
                style={{
                  background: isSelected ? "var(--terra-08)" : "var(--ds-white)",
                  border: isSelected
                    ? "2px solid var(--terra)"
                    : "1px solid var(--ink-08)",
                  borderRadius: "var(--r-lg)",
                }}
              >
                {/* Radio indicator */}
                <div
                  className="w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5 transition-colors"
                  style={{
                    border: isSelected
                      ? "none"
                      : "2px solid var(--ink-15)",
                    background: isSelected ? "var(--terra)" : "transparent",
                  }}
                >
                  {isSelected && (
                    <div
                      className="w-2 h-2 rounded-full"
                      style={{ background: "white" }}
                    />
                  )}
                </div>

                {/* Content */}
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="text-[14px] font-semibold"
                      style={{ color: "var(--ink)" }}
                    >
                      {freq.title}
                    </span>
                    {freq.recommended && (
                      <span
                        className="px-2 py-0.5 text-[10px] font-semibold rounded-full"
                        style={{
                          background: "var(--jade-15)",
                          color: "var(--jade)",
                        }}
                      >
                        Đề xuất
                      </span>
                    )}
                  </div>
                  <p
                    className="text-[12px] mt-1"
                    style={{ color: "var(--ink-50)" }}
                  >
                    {freq.description}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Footer */}
      <div className="flex-shrink-0 px-5 pt-4 pb-8">
        <button
          onClick={handleConfirm}
          disabled={loading}
          className="w-full h-[56px] text-[16px] font-bold transition-colors"
          style={{
            background: "var(--terra)",
            color: "white",
            borderRadius: "var(--r-lg)",
            opacity: loading ? 0.7 : 1,
          }}
        >
          {loading ? (
            <span className="animate-pulse">Đang tạo...</span>
          ) : (
            "Bắt đầu tìm kiếm 🚀"
          )}
        </button>
        <p
          className="text-[12px] text-center mt-3"
          style={{ color: "var(--ink-30)" }}
        >
          Mình sẽ bắt đầu quét ngay sau khi tạo xong
        </p>
      </div>
    </div>
  );
}
