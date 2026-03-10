"use client";

import { RentalCard } from "./rental-card";
import type { Listing, AreaResearch } from "@/types";

interface CardStackProps {
  listings: Listing[];
  getResearch: (listing: Listing) => AreaResearch | null;
  onSwipe: (listing: Listing, direction: "like" | "skip" | "contact") => void;
  onTap?: (listing: Listing) => void;
}

export function CardStack({ listings, getResearch, onSwipe, onTap }: CardStackProps) {
  // Show top 3 only
  const visible = listings.slice(0, 3);

  return (
    <div className="relative w-full h-full overflow-hidden">
      {visible.map((listing, index) => (
        <RentalCard
          key={listing.id}
          listing={listing}
          research={getResearch(listing)}
          stackIndex={index}
          isDraggable={index === 0}
          onSwipe={(dir) => onSwipe(listing, dir)}
          onTap={index === 0 ? () => onTap?.(listing) : undefined}
        />
      ))}
    </div>
  );
}
