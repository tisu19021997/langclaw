"use client";

import { useRef, useCallback } from "react";
import { Bed, Maximize2, Phone, Search } from "lucide-react";
import type { Listing, AreaResearch } from "@/types";

function hasContactInfo(listing: Listing): boolean {
  return !!(listing.landlord_phone || listing.landlord_zalo || listing.landlord_facebook_url);
}

interface RentalCardProps {
  listing: Listing;
  research: AreaResearch | null;
  /** 0 = top, 1 = middle, 2 = back */
  stackIndex: number;
  onSwipe: (direction: "like" | "skip" | "contact") => void;
  isDraggable: boolean;
  onTap?: () => void;
}

function ScoreArcBadge({ score }: { score: number }) {
  const circumference = 2 * Math.PI * 19; // ~119.4
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

const STACK_TRANSFORMS = [
  "", // top card
  "translateY(12px) scale(.955)",
  "translateY(24px) scale(.91)",
];

export function RentalCard({
  listing,
  research,
  stackIndex,
  onSwipe,
  isDraggable,
  onTap,
}: RentalCardProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const dragState = useRef({
    isDragging: false,
    startX: 0,
    startY: 0,
    dx: 0,
    dy: 0,
  });
  const likeRef = useRef<HTMLDivElement>(null);
  const skipRef = useRef<HTMLDivElement>(null);

  const clamp = (val: number, min: number, max: number) =>
    Math.min(Math.max(val, min), max);

  const resetCard = useCallback(() => {
    const card = cardRef.current;
    if (!card) return;
    card.style.transition = "transform 0.3s cubic-bezier(.4,0,.2,1)";
    card.style.transform = STACK_TRANSFORMS[stackIndex] || "";
    if (likeRef.current) likeRef.current.style.opacity = "0";
    if (skipRef.current) skipRef.current.style.opacity = "0";
  }, [stackIndex]);

  const animateOff = useCallback(
    (direction: "like" | "skip" | "contact") => {
      const card = cardRef.current;
      if (!card) return;
      const xTarget = direction === "skip" ? -window.innerWidth : window.innerWidth;
      card.style.transition = "transform 0.3s ease-out";
      card.style.transform = `translate(${xTarget}px, 0) rotate(${xTarget * 0.05}deg)`;
      setTimeout(() => onSwipe(direction), 280);
    },
    [onSwipe]
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!isDraggable) return;
      const card = cardRef.current;
      if (!card) return;
      card.setPointerCapture(e.pointerId);
      card.style.transition = "none";
      dragState.current = {
        isDragging: true,
        startX: e.clientX,
        startY: e.clientY,
        dx: 0,
        dy: 0,
      };
    },
    [isDraggable]
  );

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragState.current.isDragging) return;
    const dx = e.clientX - dragState.current.startX;
    const dy = e.clientY - dragState.current.startY;
    dragState.current.dx = dx;
    dragState.current.dy = dy;

    const card = cardRef.current;
    if (!card) return;
    card.style.transform = `translate(${dx}px, ${dy * 0.25}px) rotate(${dx * 0.07}deg)`;

    if (likeRef.current) {
      likeRef.current.style.opacity =
        dx > 20 ? String(clamp(dx / 90, 0, 1)) : "0";
    }
    if (skipRef.current) {
      skipRef.current.style.opacity =
        dx < -20 ? String(clamp(-dx / 90, 0, 1)) : "0";
    }
  }, []);

  const onPointerUp = useCallback(() => {
    if (!dragState.current.isDragging) return;
    dragState.current.isDragging = false;
    const { dx, dy } = dragState.current;

    if (dx > 85) {
      animateOff("like");
    } else if (dx < -85) {
      animateOff("skip");
    } else {
      if (Math.abs(dx) < 5 && Math.abs(dy) < 10) {
        onTap?.();
      }
      resetCard();
    }
  }, [animateOff, resetCard, onTap]);

  const overallScore = research?.overall_score ?? null;

  return (
    <div
      ref={cardRef}
      className="absolute inset-0 touch-none select-none"
      style={{
        borderRadius: "var(--r-xl)",
        overflow: "hidden",
        zIndex: 3 - stackIndex,
        transform: STACK_TRANSFORMS[stackIndex] || "",
        boxShadow: stackIndex === 0 ? "var(--shadow-float)" : "var(--shadow-card)",
        willChange: "transform",
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      {/* Background image */}
      <div
        className="absolute inset-0 bg-center bg-cover"
        style={{
          backgroundImage: listing.thumbnail_url
            ? `url(${listing.thumbnail_url})`
            : undefined,
          backgroundColor: listing.thumbnail_url ? undefined : "var(--cream-200)",
        }}
      />

      {/* Gradient overlay */}
      <div
        className="absolute inset-0"
        style={{
          background: "linear-gradient(transparent 40%, rgba(14,12,10,.82))",
        }}
      />

      {/* Top left badges */}
      <div className="absolute top-3 left-3 flex flex-col gap-1.5">
        {/* Source tag */}
        {listing.source_platform && (
          <div
            className="px-3 py-1 text-[11px] font-semibold text-white"
            style={{
              background: "rgba(255,255,255,.15)",
              backdropFilter: "blur(12px)",
              WebkitBackdropFilter: "blur(12px)",
              borderRadius: "var(--r-full)",
            }}
          >
            {listing.source_platform}
          </div>
        )}

        {/* Contact status chip */}
        <div
          className="flex items-center gap-1 px-2.5 py-1 text-[10px] font-semibold"
          style={{
            background: hasContactInfo(listing)
              ? "rgba(87,217,154,.85)"
              : "rgba(255,255,255,.15)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            borderRadius: "var(--r-full)",
            color: "white",
          }}
        >
          {hasContactInfo(listing) ? (
            <>
              <Phone size={10} />
              Contact found
            </>
          ) : (
            <>
              <Search size={10} />
              No contact
            </>
          )}
        </div>
      </div>

      {/* Score arc badge — top right */}
      {overallScore !== null && <ScoreArcBadge score={overallScore} />}

      {/* Swipe indicators */}
      <div
        ref={likeRef}
        className="absolute top-1/3 right-6 px-4 py-2 rounded-xl text-lg font-bold"
        style={{
          opacity: 0,
          background: "rgba(87,217,154,.85)",
          color: "white",
          border: "2px solid white",
          transform: "rotate(-12deg)",
          pointerEvents: "none",
        }}
      >
        Like
      </div>
      <div
        ref={skipRef}
        className="absolute top-1/3 left-6 px-4 py-2 rounded-xl text-lg font-bold"
        style={{
          opacity: 0,
          background: "rgba(220,60,60,.85)",
          color: "white",
          border: "2px solid white",
          transform: "rotate(12deg)",
          pointerEvents: "none",
        }}
      >
        Skip
      </div>

      {/* Card body — bottom */}
      <div className="absolute bottom-0 left-0 right-0 p-5" style={{ pointerEvents: "none" }}>
        {/* Price */}
        <div
          className="text-white mb-2"
          style={{ fontSize: 30, fontWeight: 900, letterSpacing: "-1.2px" }}
        >
          {listing.price_display || "Contact"}
        </div>

        {/* Specs chips */}
        <div className="flex gap-2 mb-2">
          {listing.bedrooms !== null && listing.bedrooms !== undefined && (
            <div
              className="flex items-center gap-1 px-2.5 py-1 text-[12px] font-medium text-white"
              style={{
                background: "rgba(255,255,255,.15)",
                borderRadius: "var(--r-full)",
                backdropFilter: "blur(8px)",
              }}
            >
              <Bed size={13} />
              {listing.bedrooms} BR
            </div>
          )}
          {listing.area_sqm !== null && listing.area_sqm !== undefined && (
            <div
              className="flex items-center gap-1 px-2.5 py-1 text-[12px] font-medium text-white"
              style={{
                background: "rgba(255,255,255,.15)",
                borderRadius: "var(--r-full)",
                backdropFilter: "blur(8px)",
              }}
            >
              <Maximize2 size={13} />
              {listing.area_sqm}m²
            </div>
          )}
        </div>

        {/* Location */}
        <div
          className="text-[13px] truncate"
          style={{ color: "rgba(255,255,255,.7)" }}
        >
          {[listing.district, listing.address].filter(Boolean).join(" · ")}
        </div>
      </div>
    </div>
  );
}

export { type RentalCardProps };
