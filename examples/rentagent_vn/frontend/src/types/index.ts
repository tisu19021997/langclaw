// ---------------------------------------------------------------------------
// Pipeline stages
// ---------------------------------------------------------------------------

export type PipelineStage =
  | "new"
  | "researching"
  | "pending_review"
  | "contacted"
  | "viewing"
  | "viewed"
  | "shortlisted"
  | "skipped";

export const PIPELINE_STAGES: {
  key: PipelineStage;
  label: string;
  color: string;
}[] = [
  { key: "new", label: "New", color: "bg-blue-500" },
  { key: "researching", label: "Researching", color: "bg-teal-500" },
  { key: "pending_review", label: "Pending Review", color: "bg-amber-500" },
  { key: "contacted", label: "Contacted", color: "bg-yellow-500" },
  { key: "viewing", label: "Viewing", color: "bg-orange-500" },
  { key: "viewed", label: "Viewed", color: "bg-purple-500" },
  { key: "shortlisted", label: "Shortlisted", color: "bg-green-500" },
  { key: "skipped", label: "Skipped", color: "bg-muted-foreground" },
];

export const SKIP_REASONS = [
  { key: "too_far", label: "Too far" },
  { key: "price", label: "Price mismatch" },
  { key: "bad_photos", label: "Poor photos" },
  { key: "wrong_area", label: "Wrong area" },
  { key: "other", label: "Other" },
] as const;

export type SkipReasonKey = (typeof SKIP_REASONS)[number]["key"];

// ---------------------------------------------------------------------------
// Data models (mirror backend)
// ---------------------------------------------------------------------------

export interface Listing {
  id: string;
  campaign_id: string;
  stage: PipelineStage;
  fingerprint?: string;
  title: string | null;
  description: string | null;
  price_vnd: number | null;
  price_display: string | null;
  deposit_vnd: number | null;
  address: string | null;
  district: string | null;
  city: string | null;
  area_sqm: number | null;
  bedrooms: number | null;
  bathrooms: number | null;
  listing_url: string | null;
  thumbnail_url: string | null;
  posted_date: string | null;
  source_platform: string | null;
  landlord_name: string | null;
  landlord_phone: string | null;
  landlord_zalo: string | null;
  landlord_facebook_url: string | null;
  landlord_contact_method: string | null;
  match_score: number | null;
  skip_reason: string | null;
  user_notes: string | null;
  scan_id: string | null;
  research_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Campaign {
  id: string;
  name: string;
  preferences: CampaignPreferences;
  sources: string[];
  scan_frequency: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface NotificationSettings {
  new_listings: boolean;
  research_done: boolean;
  price_drop: boolean;
  outreach_reminder: boolean;
}

export interface CampaignPreferences {
  district?: string;
  city?: string;
  bedrooms?: number;
  bathrooms?: number;
  min_price?: number;
  max_price?: number;
  min_area?: number;
  max_area?: number;
  property_type?: string;
  notes?: string;
  outreach_auto_send?: boolean;
  notification_settings?: NotificationSettings;
  [key: string]: unknown;
}

export interface Scan {
  id: string;
  campaign_id: string;
  job_id: string | null;
  status: "running" | "completed" | "failed";
  listings_found: number;
  new_listings: number;
  errors: Array<Record<string, unknown>>;
  started_at: string;
  completed_at: string | null;
}

export interface Activity {
  id: number;
  campaign_id: string;
  scan_id: string | null;
  event_type: string;
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface CampaignStats {
  total_listings: number;
  by_stage: Record<string, number>;
  new_today: number;
  total_scans: number;
}

// ---------------------------------------------------------------------------
// WebSocket message types
// ---------------------------------------------------------------------------

export type WSInbound = {
  type: "message";
  content: string;
  user_id: string;
  context_id: string;
};

export type WSOutbound =
  | { type: "ai"; content: string }
  | { type: "tool_progress"; content: string; metadata?: Record<string, unknown> }
  | { type: "tool_result"; content: string; metadata?: Record<string, unknown> }
  | { type: "error"; content: string }
  | { type: "pong" }
  | { type: "command"; content: string };

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: Date;
  metadata?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Scan SSE events (real-time progress streaming)
// ---------------------------------------------------------------------------

export type ScanSSEEventType =
  | "started"
  | "progress"
  | "streaming_url"
  | "error"
  | "complete"
  | "done";

export interface ScanSSEEvent {
  type: ScanSSEEventType;
  url: string | null;
  timestamp: number;
  // type-specific fields
  purpose?: string; // progress
  streaming_url?: string; // streaming_url
  run_id?: string; // progress, streaming_url
  message?: string; // error
  job_id?: string; // started
  total_urls?: number; // started
  urls?: string[]; // started
  listings_found?: number; // complete
  errors?: number; // complete
  urls_scanned?: number; // complete
}

export interface ScanProgressStep {
  id: string;
  url: string;
  purpose: string;
  timestamp: number;
  duration?: number;
  status: "running" | "done";
}

export type ScanStreamStatus =
  | "idle"
  | "connecting"
  | "streaming"
  | "complete"
  | "error";

export interface ScanStreamState {
  scanId: string | null;
  status: ScanStreamStatus;
  steps: ScanProgressStep[];
  streamingUrls: Record<string, string>;
  activeUrl: string | null;
  totalUrls: number;
  completedUrls: number;
  listingsFound: number;
  startedAt: number | null;
}

// ---------------------------------------------------------------------------
// Zalo
// ---------------------------------------------------------------------------

export interface ZaloStatus {
  connected: boolean;
  phone_number: string | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Outreach
// ---------------------------------------------------------------------------

export type OutreachStatus = "drafted" | "sent" | "failed" | "replied";

export interface OutreachMessage {
  id: string;
  listing_id: string;
  campaign_id: string;
  draft_text: string;
  final_text: string | null;
  status: OutreachStatus;
  landlord_phone: string | null;
  zalo_user_id: string | null;
  sent_at: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export const OUTREACH_STATUS_LABELS: Record<OutreachStatus, string> = {
  drafted: "Drafted",
  sent: "Sent",
  failed: "Failed",
  replied: "Replied",
};

// ---------------------------------------------------------------------------
// Area Research
// ---------------------------------------------------------------------------

export type ResearchStatus = "queued" | "running" | "done" | "failed";

export interface CriterionDetail {
  key: string;
  value: string;
}

export interface CriterionScore {
  criterion_key: string;
  score: number;
  label: string;
  highlights: string[];
  details: CriterionDetail[];
  walking_distance?: boolean;
}

export interface ResearchScores {
  overall: number;
  criteria: CriterionScore[];
}

export interface AreaResearch {
  id: string;
  listing_id: string;
  campaign_id: string;
  status: ResearchStatus;
  criteria: string[];
  scores: ResearchScores | null;
  result: Record<string, unknown> | null;
  verdict: string | null;
  overall_score: number | null;
  street_view_urls: string[];
  auto_outreach_enabled: boolean;
  auto_outreach_threshold: number | null;
  auto_outreach_conditions: Record<string, number> | null;
  auto_outreach_triggered: boolean;
  tinyfish_job_id: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResearchCriteriaOption {
  key: string;
  label: string;
  description: string;
  enabled: boolean;
}

export interface AutoOutreachConfig {
  enabled: boolean;
  threshold: number;
  must_pass: Record<string, number>;
  message_template: string;
}

export const DEFAULT_CRITERIA: ResearchCriteriaOption[] = [
  {
    key: "food_shopping",
    label: "Food & Shopping",
    description: "Restaurants, supermarkets, convenience stores",
    enabled: true,
  },
  {
    key: "healthcare",
    label: "Healthcare",
    description: "Hospitals, clinics, pharmacies",
    enabled: true,
  },
  {
    key: "education_family",
    label: "Education & Family",
    description: "Schools, daycare, kindergarten",
    enabled: true,
  },
  {
    key: "transportation",
    label: "Transportation",
    description: "Bus stops, metro, main roads",
    enabled: true,
  },
  {
    key: "entertainment_sports",
    label: "Entertainment & Sports",
    description: "Gyms, parks, cinemas",
    enabled: true,
  },
  {
    key: "street_atmosphere",
    label: "Street & Cleanliness",
    description: "Street View assessment: roads, cleanliness, greenery",
    enabled: true,
  },
  {
    key: "security",
    label: "Security",
    description: "Gates, cameras, guards, lighting",
    enabled: true,
  },
];

export type ResearchSSEEventType =
  | "started"
  | "progress"
  | "streaming_url"
  | "completed"
  | "failed"
  | "done";

export interface ResearchLiveState {
  browserUrl: string | null;
  currentStep: string | null;
  currentDetail: string | null;
}

export interface ResearchSSEEvent {
  type: ResearchSSEEventType;
  research_id: string | null;
  timestamp: number;
  listing_id?: string;
  address?: string;
  step?: string;
  detail?: string;
  browser_url?: string;
  overall_score?: number;
  verdict?: string;
  error?: string;
}
