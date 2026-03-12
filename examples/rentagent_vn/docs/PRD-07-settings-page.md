# PRD-07: Settings Page Refactor

| Field | Value |
|---|---|
| Phase | 7 |
| Status | Implemented |
| Owner | TBD |
| Last updated | March 2026 |
| Depends on | PRD-05 (mobile UX), PRD-06 (onboarding) shipped |

---

## Overview

The current Settings screen is incomplete and disconnected from the established design patterns. This PRD transforms Settings into a cohesive, full-featured configuration hub that reuses onboarding components for consistency and allows users to customize their search experience without restarting their campaign.

**Key insight:** Settings is where users return mid-campaign when their needs shift. The editing experience must feel as polished as onboarding, not like an afterthought.

---

## Goals

- Consolidate all user preferences into one discoverable location
- Reuse onboarding components (preference tags, source toggles) for visual consistency and reduced maintenance
- Expand notification controls beyond just "new listings" to cover all system events
- Provide a clear Zalo connection experience with room for future channel expansion
- Add more rental sources popular in Vietnam (thuephongtro.com, phongtot.com, tromoi.com, lozido.com)

---

## Information Architecture

```
Settings Screen
├── SEARCH
│   ├── Search Preferences Card (tap Edit → sheet with confirm-step UI)
│   └── Sources Card (tap Edit → sheet with sources-step UI)
├── SCHEDULE
│   └── Auto scan toggle + time display
├── NOTIFICATIONS
│   ├── New listings found
│   ├── Research complete
│   ├── Price drop alerts
│   └── Outreach reminders
├── CHANNELS
│   └── Zalo (connected/not connected → tap opens Zalo sheet)
└── ABOUT
    └── App version + Feedback link
```

---

## Implemented Components

### Shared Components (`components/shared/`)

| Component | Purpose |
|---|---|
| `bottom-sheet.tsx` | Reusable bottom sheet with drag handle, header, content, footer |
| `settings-toggle.tsx` | Toggle switch, SettingsRow, SettingsGroup, SettingsDivider, SettingsSectionLabel |
| `source-card.tsx` | Source card with platform colors, icons, toggle |
| `preference-tags.tsx` | Editable preference tag pills with inline editing |

### Settings Components (`components/settings/`)

| Component | Purpose |
|---|---|
| `settings-screen.tsx` | Main settings screen with all sections |
| `search-preferences-card.tsx` | Display search preferences with Edit button |
| `search-preferences-sheet.tsx` | Bottom sheet for editing search preferences |
| `sources-card.tsx` | Display enabled sources with Edit button |
| `sources-sheet.tsx` | Bottom sheet for managing sources |
| `schedule-section.tsx` | Auto scan toggle |
| `notifications-section.tsx` | 4 notification toggles |
| `channels-section.tsx` | Zalo connection row |
| `zalo-settings-sheet.tsx` | Bottom sheet for Zalo connection |
| `about-section.tsx` | Version and feedback |

---

## Default Sources

```typescript
const DEFAULT_SOURCES = [
  { url: "https://www.nhatot.com/thue-phong-tro", label: "Nhà Tốt", platform: "nhatot" },
  { url: "https://batdongsan.com.vn/cho-thue", label: "Batdongsan.com.vn", platform: "bds" },
  { url: "https://thuephongtro.com/", label: "Thuê Phòng Trọ", platform: "thuephongtro" },
  { url: "https://phongtot.com/", label: "Phòng Tốt", platform: "phongtot" },
  { url: "https://tromoi.com/", label: "Trọ Mới", platform: "tromoi" },
  { url: "https://lozido.com/", label: "LOZIDO", platform: "lozido" },
];
```

---

## Notification Settings

Added `notification_settings` to `CampaignPreferences`:

```typescript
interface NotificationSettings {
  new_listings: boolean;    // default: true
  research_done: boolean;   // default: true
  price_drop: boolean;      // default: true
  outreach_reminder: boolean; // default: false
}
```

---

## API Additions

```typescript
// Preview how many listings match new preferences
POST /api/v1/campaigns/{id}/preferences/preview
Body: CampaignPreferences
Response: { matching_count: number }
```

---

## Out of Scope

| Feature | Reason |
|---|---|
| Message templates | Deferred to later phase |
| Account/profile section | No auth system yet |
| Dark mode | Design system is cream-based; needs full variant |
| Multiple campaigns | Single campaign per user |
| Facebook/BDS connection flows | Zalo first; others are read-only sources |
| Notification time preferences | Keep simple for v1 |

---

## Success Metrics

| Metric | Target |
|---|---|
| Preference edit rate | >30% of campaigns have at least one preference edit from settings |
| Source customization | >20% of users add/remove at least one source after onboarding |
| Zalo connection from settings | >50% of Zalo connections happen through settings (vs. first-contact prompt) |
| Settings screen dwell time | Average >30 seconds (indicates exploration, not bounce) |
