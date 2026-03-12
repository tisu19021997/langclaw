"use client";

import { useState } from "react";
import type { CampaignPreferences } from "@/types";
import { useCampaignStore } from "@/stores/campaign-store";
import { toast } from "sonner";
import { ChatStep } from "./chat-step";
import { ConfirmStep } from "./confirm-step";
import { SourcesStep } from "./sources-step";
import { FrequencyStep } from "./frequency-step";

type Step = "chat" | "confirm" | "sources" | "frequency";

const STEPS: Step[] = ["chat", "confirm", "sources", "frequency"];

interface SetupWizardProps {
  onComplete: (campaignId: string) => void;
}

function ProgressDots({ currentStep }: { currentStep: Step }) {
  const currentIndex = STEPS.indexOf(currentStep);

  return (
    <div
      className="fixed top-4 left-1/2 -translate-x-1/2 z-10 flex items-center gap-2 px-4 py-2 backdrop-blur-md"
      style={{
        background: "rgba(250,247,242,.9)",
        borderRadius: "var(--r-full)",
      }}
    >
      {STEPS.map((step, i) => {
        const isActive = i === currentIndex;
        const isCompleted = i < currentIndex;

        return (
          <div
            key={step}
            className="transition-all duration-300"
            style={{
              width: isActive || isCompleted ? 8 : 6,
              height: isActive || isCompleted ? 8 : 6,
              borderRadius: "50%",
              background: isActive
                ? "var(--terra)"
                : isCompleted
                ? "rgba(196,86,42,.5)"
                : "var(--ink-15)",
            }}
          />
        );
      })}
    </div>
  );
}

export function SetupWizard({ onComplete }: SetupWizardProps) {
  const [step, setStep] = useState<Step>("chat");
  const [preferences, setPreferences] = useState<CampaignPreferences>({});
  const [sources, setSources] = useState<string[]>([]);
  // Use selector to avoid subscribing to entire store
  const createCampaign = useCampaignStore((s) => s.createCampaign);

  const handlePreferencesExtracted = (prefs: CampaignPreferences) => {
    setPreferences(prefs);
    setStep("confirm");
  };

  const handlePreferencesConfirmed = (prefs: CampaignPreferences) => {
    setPreferences(prefs);
    setStep("sources");
  };

  const handleSourcesConfirmed = (urls: string[]) => {
    setSources(urls);
    setStep("frequency");
  };

  const handleFrequencyConfirmed = async (freq: string) => {
    try {
      const campaign = await createCampaign({
        name: buildCampaignName(preferences),
        preferences,
        sources,
        scan_frequency: freq,
      });
      onComplete(campaign.id);
    } catch (e) {
      console.error("Failed to create campaign:", e);
      toast.error("Không thể tạo chiến dịch. Vui lòng thử lại.");
    }
  };

  return (
    <div className="min-h-screen flex flex-col" style={{ background: "var(--cream)" }}>
      <ProgressDots currentStep={step} />

      <div className="flex-1 flex flex-col">
        {step === "chat" && (
          <ChatStep onExtracted={handlePreferencesExtracted} />
        )}
        {step === "confirm" && (
          <ConfirmStep
            preferences={preferences}
            onConfirm={handlePreferencesConfirmed}
            onBack={() => setStep("chat")}
          />
        )}
        {step === "sources" && (
          <SourcesStep
            preferences={preferences}
            onConfirm={handleSourcesConfirmed}
            onBack={() => setStep("confirm")}
          />
        )}
        {step === "frequency" && (
          <FrequencyStep
            onConfirm={handleFrequencyConfirmed}
            onBack={() => setStep("sources")}
          />
        )}
      </div>
    </div>
  );
}

function buildCampaignName(prefs: CampaignPreferences): string {
  const parts: string[] = [];
  if (prefs.property_type) parts.push(prefs.property_type);
  if (prefs.district) parts.push(prefs.district);
  if (prefs.bedrooms) parts.push(`${prefs.bedrooms}PN`);
  return parts.length > 0 ? parts.join(" · ") : "Chiến dịch mới";
}
