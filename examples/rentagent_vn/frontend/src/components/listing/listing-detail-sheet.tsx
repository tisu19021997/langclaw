"use client";

import { useState } from "react";
import {
  X,
  Heart,
  Zap,
  MapPin,
  Bed,
  Bath,
  Maximize2,
  ExternalLink,
  Phone,
  MessageCircle,
} from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { ResearchResults } from "@/components/dashboard/research-results";
import { ResearchProgress } from "@/components/dashboard/research-progress";
import { OutreachDialog } from "@/components/dashboard/outreach-dialog";
import { ZaloSettingsDialog } from "@/components/zalo/zalo-settings-dialog";
import { useListingStore } from "@/stores/listing-store";
import { useResearchStore } from "@/stores/research-store";
import type { Listing } from "@/types";

interface ListingDetailSheetProps {
  open: boolean;
  onClose: () => void;
  listing: Listing;
  campaignId: string;
  mode: "discover" | "track";
  onLike?: () => void;
  onSkip?: () => void;
  onContact?: () => void;
}

function ScoreArcBadge({ score }: { score: number }) {
  const circumference = 2 * Math.PI * 19;
  const offset = circumference * (1 - score / 10);
  const strokeColor = score >= 8.0 ? "#57d99a" : "#f0b860";

  return (
    <div className="absolute top-3 right-3" style={{ width: 54, height: 54 }}>
      <svg viewBox="0 0 54 54" fill="none">
        <circle cx="27" cy="27" r="19" fill="rgba(0,0,0,.35)" />
        <circle
          cx="27"
          cy="27"
          r="19"
          fill="none"
          stroke="rgba(255,255,255,.15)"
          strokeWidth="3"
          strokeDasharray={circumference}
        />
        <circle
          cx="27"
          cy="27"
          r="19"
          fill="none"
          stroke={strokeColor}
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transform: "rotate(-90deg)", transformOrigin: "center" }}
        />
        <text
          x="27"
          y="31"
          textAnchor="middle"
          fill="white"
          fontSize="14"
          fontWeight="800"
        >
          {score.toFixed(1)}
        </text>
      </svg>
    </div>
  );
}

function InfoItem({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div
      className="flex items-center gap-2 p-2.5 rounded-lg"
      style={{ background: "var(--ink-04)" }}
    >
      <div style={{ color: "var(--ink-50)" }}>{icon}</div>
      <div className="min-w-0">
        <p className="text-[11px]" style={{ color: "var(--ink-50)" }}>
          {label}
        </p>
        <p
          className="text-[13px] font-medium truncate"
          style={{ color: "var(--ink)" }}
        >
          {value}
        </p>
      </div>
    </div>
  );
}

export function ListingDetailSheet({
  open,
  onClose,
  listing,
  campaignId,
  mode,
  onLike,
  onSkip,
  onContact,
}: ListingDetailSheetProps) {
  const { updateNotes, fetchListings } = useListingStore();
  const { researching, researchByListing } = useResearchStore();
  const [descExpanded, setDescExpanded] = useState(false);
  const [notes, setNotes] = useState(listing.user_notes || "");
  const [outreachOpen, setOutreachOpen] = useState(false);
  const [zaloSettingsOpen, setZaloSettingsOpen] = useState(false);

  const researchId = listing.research_id ?? researchByListing[listing.id];
  const research = researchId ? researching[researchId] : null;
  const overallScore = research?.overall_score ?? null;

  const handleNotesBlur = async () => {
    if (notes !== (listing.user_notes || "")) {
      await updateNotes(campaignId, listing.id, notes);
    }
  };

  const handleActionAndClose = (action: (() => void) | undefined) => {
    onClose();
    setTimeout(() => action?.(), 100);
  };

  const hasLandlordContact =
    listing.landlord_phone || listing.landlord_zalo || listing.landlord_facebook_url;

  return (
    <>
      <DialogPrimitive.Root open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogPrimitive.Portal>
          {/* Overlay */}
          <DialogPrimitive.Overlay
            className="fixed inset-0 z-50 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0"
            style={{ background: "rgba(26,24,21,.6)" }}
          />

          {/* Modal content */}
          <DialogPrimitive.Content
            className="fixed z-50 outline-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
            style={{
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              width: "calc(100% - 32px)",
              maxWidth: 480,
              maxHeight: "calc(100vh - 48px)",
              borderRadius: "var(--r-xl)",
              background: "var(--ds-white)",
              boxShadow: "var(--shadow-float)",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            {/* Header with close button */}
            <div
              className="flex-shrink-0 flex items-center justify-between px-5 py-3"
              style={{ borderBottom: "1px solid var(--ink-08)" }}
            >
              <DialogPrimitive.Title
                className="text-[16px] font-bold truncate pr-3"
                style={{ color: "var(--ink)" }}
              >
                {listing.title || "Chi tiết tin đăng"}
              </DialogPrimitive.Title>
              <button
                onClick={onClose}
                className="flex-shrink-0 flex items-center justify-center rounded-full transition-colors hover:bg-[var(--ink-08)]"
                style={{
                  width: 32,
                  height: 32,
                  color: "var(--ink-50)",
                }}
              >
                <X size={18} />
              </button>
            </div>

            {/* Scrollable content */}
            <div
              className="flex-1 overflow-y-auto overscroll-contain"
              style={{ minHeight: 0 }}
            >
              {/* Image hero - constrained height */}
              <div
                className="relative w-full flex-shrink-0"
                style={{ height: 200 }}
              >
                {listing.thumbnail_url ? (
                  <div
                    className="absolute inset-0 bg-center bg-cover"
                    style={{ backgroundImage: `url(${listing.thumbnail_url})` }}
                  />
                ) : (
                  <div
                    className="absolute inset-0 flex items-center justify-center"
                    style={{
                      background:
                        "linear-gradient(135deg, var(--cream-200) 0%, var(--cream-100) 100%)",
                    }}
                  >
                    <span className="text-[13px]" style={{ color: "var(--ink-30)" }}>
                      Chưa có hình ảnh
                    </span>
                  </div>
                )}

                {/* Gradient overlay */}
                <div
                  className="absolute inset-0"
                  style={{
                    background: "linear-gradient(transparent 50%, rgba(14,12,10,.5))",
                  }}
                />

                {/* Source platform pill */}
                {listing.source_platform && (
                  <div
                    className="absolute top-3 left-3 px-2.5 py-1 text-[11px] font-semibold text-white"
                    style={{
                      background: "rgba(255,255,255,.2)",
                      backdropFilter: "blur(12px)",
                      borderRadius: "var(--r-full)",
                    }}
                  >
                    {listing.source_platform}
                  </div>
                )}

                {/* Score arc badge */}
                {overallScore !== null && <ScoreArcBadge score={overallScore} />}
              </div>

              {/* Content */}
              <div className="px-5 py-4 space-y-4">
                {/* Price + title */}
                <div>
                  <div
                    className="text-[24px] font-extrabold"
                    style={{ color: "var(--ink)", letterSpacing: "-0.8px" }}
                  >
                    {listing.price_display || "Liên hệ"}
                  </div>
                  {listing.deposit_vnd && (
                    <div
                      className="text-[13px] mt-0.5"
                      style={{ color: "var(--ink-50)" }}
                    >
                      Cọc: {(listing.deposit_vnd / 1_000_000).toFixed(0)} triệu
                    </div>
                  )}
                  {listing.title && (
                    <p
                      className="text-[15px] font-semibold mt-2 line-clamp-2"
                      style={{ color: "var(--ink)", lineHeight: "1.35" }}
                    >
                      {listing.title}
                    </p>
                  )}
                </div>

                {/* Specs grid */}
                <div className="grid grid-cols-2 gap-2">
                  {listing.area_sqm != null && (
                    <InfoItem
                      icon={<Maximize2 className="h-4 w-4" />}
                      label="Diện tích"
                      value={`${listing.area_sqm} m²`}
                    />
                  )}
                  {listing.bedrooms != null && (
                    <InfoItem
                      icon={<Bed className="h-4 w-4" />}
                      label="Phòng ngủ"
                      value={`${listing.bedrooms} PN`}
                    />
                  )}
                  {listing.bathrooms != null && (
                    <InfoItem
                      icon={<Bath className="h-4 w-4" />}
                      label="Phòng tắm"
                      value={`${listing.bathrooms} PT`}
                    />
                  )}
                  {listing.district && (
                    <InfoItem
                      icon={<MapPin className="h-4 w-4" />}
                      label="Khu vực"
                      value={listing.district}
                    />
                  )}
                </div>

                {/* Address */}
                {listing.address && (
                  <div className="flex items-start gap-2">
                    <MapPin
                      className="h-4 w-4 mt-0.5 flex-shrink-0"
                      style={{ color: "var(--ink-50)" }}
                    />
                    <p className="text-[13px]" style={{ color: "var(--ink-70)" }}>
                      {listing.address}
                    </p>
                  </div>
                )}

                {/* Match score pill */}
                {listing.match_score != null && listing.match_score >= 60 && (
                  <div
                    className="inline-flex items-center px-3 py-1.5 rounded-full text-[12px] font-semibold"
                    style={{ background: "var(--jade-15)", color: "var(--jade)" }}
                  >
                    Phù hợp {listing.match_score}%
                  </div>
                )}

                {/* Description */}
                {listing.description && (
                  <div>
                    <p
                      className={`text-[13px] leading-relaxed ${
                        descExpanded ? "" : "line-clamp-4"
                      }`}
                      style={{ color: "var(--ink-70)" }}
                    >
                      {listing.description}
                    </p>
                    {listing.description.length > 200 && (
                      <button
                        onClick={() => setDescExpanded(!descExpanded)}
                        className="text-[13px] font-medium mt-1"
                        style={{ color: "var(--terra)" }}
                      >
                        {descExpanded ? "Thu gọn" : "Xem thêm"}
                      </button>
                    )}
                  </div>
                )}

                {/* Research results */}
                {research && research.status === "done" && research.scores && (
                  <div
                    className="rounded-xl p-4"
                    style={{
                      background: "var(--jade-15)",
                      border: "1px solid var(--jade)",
                    }}
                  >
                    <ResearchResults
                      research={research}
                      campaignId={campaignId}
                      listingId={listing.id}
                    />
                  </div>
                )}

                {research &&
                  (research.status === "running" || research.status === "queued") && (
                    <div
                      className="rounded-xl p-4"
                      style={{ border: "1px solid var(--ink-08)" }}
                    >
                      <ResearchProgress research={research} />
                    </div>
                  )}

                {/* Contact section (track mode only) */}
                {mode === "track" && hasLandlordContact && (
                  <div
                    className="rounded-xl p-4 space-y-3"
                    style={{ background: "var(--cream-100)" }}
                  >
                    <p
                      className="text-[13px] font-semibold"
                      style={{ color: "var(--ink)" }}
                    >
                      Thông tin liên hệ
                    </p>
                    {listing.landlord_name && (
                      <p className="text-[13px]" style={{ color: "var(--ink-70)" }}>
                        {listing.landlord_name}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      {listing.landlord_phone && (
                        <a
                          href={`tel:${listing.landlord_phone}`}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium"
                          style={{
                            background: "var(--ds-white)",
                            border: "1px solid var(--ink-15)",
                            color: "var(--ink)",
                          }}
                        >
                          <Phone className="h-3.5 w-3.5" />
                          {listing.landlord_phone}
                        </a>
                      )}
                      {listing.landlord_zalo && (
                        <a
                          href={`https://zalo.me/${listing.landlord_zalo}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium"
                          style={{
                            background: "var(--ds-white)",
                            border: "1px solid var(--ink-15)",
                            color: "var(--ink)",
                          }}
                        >
                          <MessageCircle className="h-3.5 w-3.5" />
                          Zalo
                        </a>
                      )}
                      {listing.landlord_facebook_url && (
                        <a
                          href={listing.landlord_facebook_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium"
                          style={{
                            background: "var(--ds-white)",
                            border: "1px solid var(--ink-15)",
                            color: "var(--ink)",
                          }}
                        >
                          Facebook
                        </a>
                      )}
                    </div>
                  </div>
                )}

                {/* Notes (track mode only) */}
                {mode === "track" && (
                  <div className="space-y-2">
                    <label
                      className="text-[13px] font-semibold"
                      style={{ color: "var(--ink)" }}
                    >
                      Ghi chú
                    </label>
                    <textarea
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      onBlur={handleNotesBlur}
                      placeholder="Thêm ghi chú về căn hộ này..."
                      rows={3}
                      className="w-full px-3 py-2.5 rounded-xl text-[13px] resize-none"
                      style={{
                        background: "var(--cream)",
                        border: "1px solid var(--ink-15)",
                        color: "var(--ink)",
                      }}
                    />
                  </div>
                )}

                {/* Source footer */}
                <div
                  className="flex items-center justify-between pt-2"
                  style={{ borderTop: "1px solid var(--ink-08)" }}
                >
                  <div className="flex items-center gap-2">
                    {listing.source_platform && (
                      <span
                        className="px-2 py-0.5 rounded text-[11px] font-medium"
                        style={{ background: "var(--ink-08)", color: "var(--ink-50)" }}
                      >
                        {listing.source_platform}
                      </span>
                    )}
                    {listing.posted_date && (
                      <span className="text-[11px]" style={{ color: "var(--ink-30)" }}>
                        {new Date(listing.posted_date).toLocaleDateString("vi-VN")}
                      </span>
                    )}
                  </div>
                  {listing.listing_url && (
                    <a
                      href={listing.listing_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-[12px] font-medium"
                      style={{ color: "var(--terra)" }}
                    >
                      Xem bài gốc
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
              </div>
            </div>

            {/* Sticky action footer */}
            <div
              className="flex-shrink-0"
              style={{
                borderTop: "1px solid var(--ink-08)",
                background: "var(--ds-white)",
              }}
            >
              {mode === "discover" ? (
                <div
                  className="flex items-center justify-center gap-5"
                  style={{ padding: "16px 32px 20px" }}
                >
                  {/* Skip */}
                  <div className="flex flex-col items-center gap-1.5">
                    <button
                      onClick={() => handleActionAndClose(onSkip)}
                      className="flex items-center justify-center rounded-full transition-transform active:scale-95"
                      style={{
                        width: 54,
                        height: 54,
                        background: "var(--ds-white)",
                        border: "1px solid var(--ink-15)",
                        boxShadow: "var(--shadow-card)",
                      }}
                    >
                      <X size={22} style={{ color: "var(--ink-50)" }} />
                    </button>
                    <span
                      className="text-[11px] font-medium"
                      style={{ color: "var(--ink-30)" }}
                    >
                      Bỏ qua
                    </span>
                  </div>

                  {/* Like */}
                  <div className="flex flex-col items-center gap-1.5">
                    <button
                      onClick={() => handleActionAndClose(onLike)}
                      className="flex items-center justify-center rounded-full transition-transform active:scale-95"
                      style={{
                        width: 64,
                        height: 64,
                        background: "var(--terra)",
                        boxShadow: "var(--shadow-float)",
                      }}
                    >
                      <Heart size={26} fill="white" color="white" />
                    </button>
                    <span
                      className="text-[11px] font-medium"
                      style={{ color: "var(--terra)" }}
                    >
                      Xem thêm
                    </span>
                  </div>

                  {/* Contact */}
                  <div className="flex flex-col items-center gap-1.5">
                    <button
                      onClick={() => handleActionAndClose(onContact)}
                      className="flex items-center justify-center rounded-full transition-transform active:scale-95"
                      style={{
                        width: 46,
                        height: 46,
                        background: "var(--ds-white)",
                        border: "1px solid var(--ink-15)",
                        boxShadow: "var(--shadow-card)",
                      }}
                    >
                      <Zap size={18} style={{ color: "var(--ink-50)" }} />
                    </button>
                    <span
                      className="text-[11px] font-medium"
                      style={{ color: "var(--ink-30)" }}
                    >
                      Liên hệ luôn
                    </span>
                  </div>
                </div>
              ) : (
                <div className="px-5 py-4 space-y-3">
                  {hasLandlordContact && (
                    <button
                      onClick={() => setOutreachOpen(true)}
                      className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-[14px] font-semibold text-white transition-transform active:scale-[0.98]"
                      style={{ background: "var(--terra)" }}
                    >
                      <MessageCircle className="h-4 w-4" />
                      Liên hệ chủ nhà
                    </button>
                  )}
                  {listing.listing_url && (
                    <a
                      href={listing.listing_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-[14px] font-semibold transition-transform active:scale-[0.98]"
                      style={{
                        background: "var(--ink-04)",
                        color: "var(--ink)",
                      }}
                    >
                      <ExternalLink className="h-4 w-4" />
                      Xem bài gốc
                    </a>
                  )}
                </div>
              )}
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      {/* Outreach dialog (track mode) */}
      <OutreachDialog
        open={outreachOpen}
        onClose={() => setOutreachOpen(false)}
        listing={listing}
        campaignId={campaignId}
        onZaloSettingsOpen={() => {
          setOutreachOpen(false);
          setZaloSettingsOpen(true);
        }}
        onSuccess={() => {
          fetchListings(campaignId);
        }}
      />

      {/* Zalo settings dialog */}
      <ZaloSettingsDialog
        open={zaloSettingsOpen}
        onClose={() => setZaloSettingsOpen(false)}
      />
    </>
  );
}
