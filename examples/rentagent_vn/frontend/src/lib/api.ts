/**
 * REST API client for RentAgent VN backend.
 */

import type {
  Activity,
  AreaResearch,
  Campaign,
  CampaignPreferences,
  CampaignStats,
  Listing,
  OutreachMessage,
  PipelineStage,
  Scan,
  ZaloStatus,
} from "@/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Campaigns
// ---------------------------------------------------------------------------

export async function createCampaign(data: {
  name?: string;
  preferences?: CampaignPreferences;
  sources?: string[];
  scan_frequency?: string;
}): Promise<Campaign> {
  return request<Campaign>("/api/v1/campaigns", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function listCampaigns(): Promise<Campaign[]> {
  return request<Campaign[]>("/api/v1/campaigns");
}

export async function getCampaign(id: string): Promise<Campaign> {
  return request<Campaign>(`/api/v1/campaigns/${id}`);
}

export async function updateCampaign(
  id: string,
  data: Partial<{
    name: string;
    preferences: CampaignPreferences;
    sources: string[];
    scan_frequency: string;
    status: string;
  }>
): Promise<Campaign> {
  return request<Campaign>(`/api/v1/campaigns/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

// ---------------------------------------------------------------------------
// Listings
// ---------------------------------------------------------------------------

export async function getListings(
  campaignId: string,
  stage?: PipelineStage
): Promise<Listing[]> {
  const params = stage ? `?stage=${stage}` : "";
  return request<Listing[]>(
    `/api/v1/campaigns/${campaignId}/listings${params}`
  );
}

export async function getListing(
  campaignId: string,
  listingId: string
): Promise<Listing> {
  return request<Listing>(
    `/api/v1/campaigns/${campaignId}/listings/${listingId}`
  );
}

export async function updateListing(
  campaignId: string,
  listingId: string,
  data: {
    stage?: PipelineStage;
    skip_reason?: string;
    user_notes?: string;
  }
): Promise<Listing> {
  return request<Listing>(
    `/api/v1/campaigns/${campaignId}/listings/${listingId}`,
    { method: "PATCH", body: JSON.stringify(data) }
  );
}

// ---------------------------------------------------------------------------
// Scans
// ---------------------------------------------------------------------------

export async function triggerScan(
  campaignId: string,
  query?: string
): Promise<Scan> {
  return request<Scan>(`/api/v1/campaigns/${campaignId}/scan`, {
    method: "POST",
    body: JSON.stringify(query ? { query } : {}),
  });
}

export async function getScans(
  campaignId: string,
  limit = 10
): Promise<Scan[]> {
  return request<Scan[]>(
    `/api/v1/campaigns/${campaignId}/scans?limit=${limit}`
  );
}

// ---------------------------------------------------------------------------
// Activity
// ---------------------------------------------------------------------------

export async function getActivities(
  campaignId: string,
  limit = 50
): Promise<Activity[]> {
  return request<Activity[]>(
    `/api/v1/campaigns/${campaignId}/activity?limit=${limit}`
  );
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

export async function getCampaignStats(
  campaignId: string
): Promise<CampaignStats> {
  return request<CampaignStats>(
    `/api/v1/campaigns/${campaignId}/stats`
  );
}

// ---------------------------------------------------------------------------
// Zalo
// ---------------------------------------------------------------------------

export async function getZaloStatus(): Promise<ZaloStatus> {
  return request<ZaloStatus>("/api/v1/zalo/status");
}

export async function connectZaloCookie(
  cookie: string,
  imei: string,
  userAgent: string
): Promise<ZaloStatus> {
  return request<ZaloStatus>("/api/v1/zalo/auth/cookie", {
    method: "POST",
    body: JSON.stringify({ cookie, imei, user_agent: userAgent }),
  });
}

export async function connectZaloQR(): Promise<{ qr_path: string }> {
  return request<{ qr_path: string }>("/api/v1/zalo/auth/qr", {
    method: "POST",
  });
}

export async function disconnectZalo(): Promise<ZaloStatus> {
  return request<ZaloStatus>("/api/v1/zalo/logout", {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Outreach
// ---------------------------------------------------------------------------

export async function draftOutreach(
  campaignId: string,
  listingId: string,
  customNotes?: string
): Promise<OutreachMessage> {
  return request<OutreachMessage>(
    `/api/v1/campaigns/${campaignId}/listings/${listingId}/outreach`,
    {
      method: "POST",
      body: JSON.stringify(customNotes ? { custom_notes: customNotes } : {}),
    }
  );
}

export async function sendOutreach(
  campaignId: string,
  listingId: string,
  messageId: string,
  finalText?: string
): Promise<OutreachMessage> {
  return request<OutreachMessage>(
    `/api/v1/campaigns/${campaignId}/listings/${listingId}/outreach/send`,
    {
      method: "POST",
      body: JSON.stringify({
        message_id: messageId,
        ...(finalText ? { final_text: finalText } : {}),
      }),
    }
  );
}

export async function getOutreachHistory(
  campaignId: string,
  listingId: string
): Promise<OutreachMessage[]> {
  return request<OutreachMessage[]>(
    `/api/v1/campaigns/${campaignId}/listings/${listingId}/outreach`
  );
}

// ---------------------------------------------------------------------------
// Area Research
// ---------------------------------------------------------------------------

export async function triggerResearch(
  campaignId: string,
  body: {
    listing_ids: string[];
    criteria?: string[];
    auto_outreach?: {
      enabled: boolean;
      threshold: number;
      must_pass: Record<string, number>;
      message_template?: string;
    };
  }
): Promise<{ research_ids: string[]; status: string; message: string }> {
  return request(`/api/v1/campaigns/${campaignId}/research`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function listResearch(
  campaignId: string,
  status?: string
): Promise<AreaResearch[]> {
  const params = status ? `?status=${status}` : "";
  return request<AreaResearch[]>(
    `/api/v1/campaigns/${campaignId}/research${params}`
  );
}

export async function getResearch(
  campaignId: string,
  researchId: string
): Promise<AreaResearch> {
  return request<AreaResearch>(
    `/api/v1/campaigns/${campaignId}/research/${researchId}`
  );
}

export async function retryResearch(
  campaignId: string,
  researchId: string
): Promise<AreaResearch> {
  return request<AreaResearch>(
    `/api/v1/campaigns/${campaignId}/research/${researchId}/retry`,
    { method: "POST" }
  );
}

// ---------------------------------------------------------------------------
// Preferences Preview
// ---------------------------------------------------------------------------

export async function previewPreferences(
  campaignId: string,
  preferences: CampaignPreferences
): Promise<{ matching_count: number }> {
  return request<{ matching_count: number }>(
    `/api/v1/campaigns/${campaignId}/preferences/preview`,
    {
      method: "POST",
      body: JSON.stringify(preferences),
    }
  );
}
