# PRD-05: Mobile UX Refactor — Swipe-First Experience

**Status:** Ready to build
**Deadline:** 1–2 days
**Stack:** Next.js 16 / React 19 / TypeScript / Tailwind 4 / Zustand 5
**Audience:** Solo dev + Claude Code agents

---

## 1. Overview

### Problem

The current UI is a desktop-first kanban board. It front-loads every piece of information at once — 6 columns, 60+ cards, sidebar details — and forces users into an "admin" mental model before they've even decided anything. Users are Vietnamese Gen Z on their phone in the evening. They want to decide fast, not manage a pipeline.

### Goal

Replace the `Dashboard` kanban with a **3-tab mobile-first experience**:

- **Khám phá** — Tinder-style card stack for new listings. One decision at a time.
- **Theo dõi** — Flat 3-section list for listings already in the pipeline.
- **Cài đặt** — Connections, scan schedule, active search query.

### Success criteria

- A user can swipe through all new listings in under 2 minutes on mobile
- Swiping right immediately triggers area research in the background (no extra tap)
- Research status is visible at a glance in Theo dõi without opening a detail panel
- The app runs correctly at 390px viewport width

---

## 2. Design Reference

| Artifact | Path | Purpose |
|---|---|---|
| Interactive prototype | `examples/rentagent_vn/../redesign-concept.html` | Visual + interaction reference. Open in browser. |
| Design system | `examples/rentagent_vn/../.interface-design/system.md` | All tokens, component patterns, spacing scale, arc badge math |

**All visual decisions are already made.** Do not deviate from the design system without a documented reason. Token names in this PRD (e.g. `--terra`, `--ink-30`) reference `system.md` exactly.

---

## 3. What's Changing

| Area | Before | After |
|---|---|---|
| Root component | `Dashboard` (kanban) | `App` (3-tab layout) |
| New listing UX | Kanban column "Mới" — all cards at once | `DiscoverScreen` — one card at a time, swipe gesture |
| Research trigger | Manual button in listing detail panel | Automatic on swipe-right (Xem thêm) |
| Pipeline view | 6 kanban columns | 3 flat sections in `TrackScreen` |
| Settings | Buried in sidebar | Dedicated `SettingsScreen` tab |
| Viewport target | Desktop (1200px+) | Mobile-first (390px) |

### Pipeline stage mapping (no backend changes)

The existing 7 stages in the DB are preserved. Only the frontend grouping changes.

| DB stage | New UI section | Tab |
|---|---|---|
| `new` | Discovery feed (swipe cards) | Khám phá |
| `researching` | Đang xem xét | Theo dõi |
| `contacted`, `viewing` | Đã liên hệ | Theo dõi |
| `viewed`, `shortlisted` | Xong | Theo dõi |
| `skipped` | Hidden (not shown) | — |

---

## 4. Frontend Architecture

### New component tree

```
src/app/page.tsx               ← unchanged (SetupWizard | App)
src/components/
  app/
    app.tsx                    ← NEW: root shell, bottom nav, tab state
  ui/
    bottom-sheet.tsx           ← NEW: reusable sheet wrapper (handle, slide-up, dismiss)
  discover/
    discover-screen.tsx        ← NEW: full discover tab
    card-stack.tsx             ← NEW: card stack container + drag logic
    rental-card.tsx            ← NEW: single card (image, price, specs, arc badge)
    action-bar.tsx             ← NEW: skip / like / contact buttons
    empty-discover.tsx         ← NEW: empty state when queue is clear
    scan-live-sheet.tsx        ← NEW: bottom sheet with tinyfish scan iframe + progress
  track/
    track-screen.tsx           ← NEW: full track tab
    track-section.tsx          ← NEW: section header + card list
    track-card.tsx             ← NEW: horizontal card with research badge + live tap
    research-live-sheet.tsx    ← NEW: bottom sheet with tinyfish research iframe + step log
  settings/
    settings-screen.tsx        ← NEW: full settings tab
    search-query-card.tsx      ← NEW: active query display + edit
    connections-section.tsx    ← NEW: Zalo, FB, BDS status + connect
    schedule-section.tsx       ← NEW: scan schedule + notification toggles
```

### What to delete

| Component | Action |
|---|---|
| `src/components/dashboard/` | **Delete entirely** |
| All kanban-related components | **Delete** |
| `src/components/listing/` detail panel | **Keep for Phase 2** (card detail screen) — do not delete, just don't render it |

### Stores — no new stores needed

All required data is already in existing stores. Wire new components to:

| Store | Used by |
|---|---|
| `useCampaignStore` | App shell (get active campaign) |
| `useListingStore` | DiscoverScreen (fetch `stage=new`), TrackScreen (fetch all non-new/non-skipped) |
| `useResearchStore` | TrackScreen (research status + scores) |
| `useScanStreamStore` | DiscoverScreen header (scan running indicator) |
| `useZaloStore` | SettingsScreen (connection status) |

---

## 5. Screen Specs

### 5.1 App Shell (`app.tsx`)

Wraps all three screens. Manages `activeTab` state (`"discover" | "track" | "settings"`).

**Layout:**
```
flex-col, h-screen, bg: --cream
├── [active screen]   flex-1, overflow-hidden
└── BottomNav         height: 80px, border-top: 1px solid --ink-08
```

**Bottom nav items (left → right):**

| Tab | Icon | Label |
|---|---|---|
| discover | Heart (lucide `Heart`) | Khám phá |
| track | Grid 2×2 (lucide `LayoutGrid`) | Theo dõi |
| settings | Gear (lucide `Settings`) | Cài đặt |

Active state: icon + label color → `--terra`. Inactive: `--ink-30`.

**On mount:** fetch listings for active campaign. Show `SetupWizard` if no active campaign (unchanged from current `page.tsx` logic).

---

### 5.2 Discover Screen (`discover-screen.tsx`)

**Layout:** `flex-col, h-full, overflow-hidden, bg: --cream`

```
DiscoverHeader         flex-shrink: 0, padding: 12px 20px
CardStack area         flex: 1, overflow: hidden, padding: 0 16px
ActionBar              flex-shrink: 0, padding: 16px 32px 20px
```

#### DiscoverHeader

Left: `SearchPill` — shows active campaign preferences as text: `"{district} · {bedrooms}PN · ≤ {max_price/1M}tr"`. Tapping opens campaign edit (Phase 2 — make it non-interactive for now with a `TODO` comment).

Right:
- Count badge: `"{n} mới"` — count of listings with `stage=new`. Background `--terra`, text white.
- Filter icon button: non-interactive for now (Phase 2).

#### CardStack

Renders the top 3 listings from `stage=new` as a stacked card effect:

| Position | z-index | Transform |
|---|---|---|
| Top (index 0) | 3 | none |
| Middle (index 1) | 2 | `translateY(12px) scale(.955)` |
| Back (index 2) | 1 | `translateY(24px) scale(.91)` |

When top card is removed (swiped or button-tapped):
1. Animate top card off screen
2. Middle card animates to top position (transition: `transform .3s cubic-bezier(.4,0,.2,1)`)
3. Back card animates to middle position
4. New card (index 3 in queue) appears at back position with no animation

**RentalCard layout** (each card, `border-radius: 26px`, `overflow: hidden`):
```
position: absolute, inset: 0
├── card-bg           background-image: listing.thumbnail_url, cover
├── card-gradient     linear-gradient transparent→rgba(14,12,10,.82)
├── source-tag        top-left, frosted pill: listing.source_platform
├── ScoreArcBadge     top-right, only if listing.research?.overall_score exists
├── SwipeIndicators   "Thêm" (right, green) / "Bỏ" (left, red), opacity driven by drag
└── card-body (bottom)
    ├── price         "{price_display}" — 30px/900 weight
    ├── specs chips   bedrooms, area_sqm
    └── location      district + address snippet
```

**ScoreArcBadge** — see `system.md` for full SVG implementation. Score ≥ 8.0 → stroke `#57d99a`, < 8.0 → `#f0b860`. Only render if `research?.overall_score` is not null.

#### Discover card — field mapping

Every `Listing` field from the API is accounted for below. Developers should not guess.

| Field | Shown? | Where / How |
|---|---|---|
| `thumbnail_url` | ✅ | Full card background, `object-fit: cover`. **Null fallback:** warm gradient `linear-gradient(135deg, #E8DDD1 0%, #C4B5A5 100%)` — no placeholder icon. |
| `source_platform` | ✅ | Top-left frosted pill. Text: raw value (e.g. "batdongsan.com.vn" → show as-is, no truncation). Pill: `bg: rgba(255,255,255,.18) backdrop-blur(8px)`, 11px/500. |
| `price_display` | ✅ | Bottom card body — 30px/900 weight, white text. |
| `bedrooms` | ✅ | Spec chip — `"{bedrooms} PN"`. Hide chip if null. |
| `area_sqm` | ✅ | Spec chip — `"{area_sqm}m²"`. Hide chip if null. |
| `district` | ✅ | Location row below specs — `"{district}"`. |
| `address` | ✅ | Location row — `"{address}"` as subtitle in `--ink-50` white equiv. Show first 40 chars only. Hide if null. |
| `match_score` | ✅ | Bottom-left of card — pill `"Phù hợp {match_score}%"`. Background: `rgba(255,255,255,.18)`, 11px/500. **Only show if `match_score ≥ 60`** (low scores are noise). Hide if null. |
| `posted_date` | ✅ (conditional) | Bottom-right corner — freshness label, 11px, `--ink-30` white equiv. Logic: `if within 24h → "Mới hôm nay"`, `if within 3 days → "{n} ngày trước"`, `if older → hide`. |
| `research?.overall_score` | ✅ (conditional) | Top-right `ScoreArcBadge` — only if research exists for this listing (from `useResearchStore` keyed by `listing.research_id`). Most `stage=new` listings won't have this. |
| `title` | ❌ | Not shown on swipe card — image-first design. Price is the identity signal. Shown in Phase 2 detail screen. |
| `description` | ❌ | Too long for a swipe card. Phase 2 detail screen. |
| `price_vnd` | ❌ | Use `price_display` (pre-formatted string from scraper). |
| `deposit_vnd` | ❌ | Not shown (secondary info, shown in Phase 2 detail). |
| `landlord_name/phone/zalo/facebook_url/contact_method` | ❌ | Not shown — decision is made in swipe, contact info surfaces after. Phase 2 detail screen. |
| `city` | ❌ | Always "Ho Chi Minh" — redundant, skip. |
| `listing_url` | ❌ | Not shown on card. Available via Phase 2 detail ("Xem nguồn" link). |
| `stage`, `scan_id`, `fingerprint`, `id` | ❌ | Internal — never shown in UI. |

**Drag interaction:**

Use `pointer` events (works on both touch and mouse):

```typescript
onPointerDown → record startX, startY
onPointerMove → dx = clientX - startX
               card.style.transform = `translate(${dx}px, ${dy * 0.25}px) rotate(${dx * 0.07}deg)`
               likeIndicator.opacity = dx > 20 ? clamp(dx/90, 0, 1) : 0
               skipIndicator.opacity = dx < -20 ? clamp(-dx/90, 0, 1) : 0
onPointerUp   → if dx > 85: animateOff('like')
               elif dx < -85: animateOff('skip')
               else: spring back
```

Call `card.setPointerCapture(e.pointerId)` on pointerdown so drags don't lose tracking.

**Empty state** (when `stage=new` count is 0):
- Center icon wrap + "Đã xem hết rồi!" heading + "Bot sẽ quét thêm tin mới..." subtext
- Hide `ActionBar` when empty state is shown

#### ActionBar

Three buttons, horizontally centered with `gap: 20px`:

| Button | Size | Style | Action |
|---|---|---|---|
| Skip (X) | 54px circle | White bg, `--ink-15` border | `animateOff('skip')` |
| Like / Xem thêm (Heart) | 64px circle | `--terra` bg | `animateOff('like')` |
| Contact / Liên hệ luôn (Bolt) | 46px circle | White bg, `--ink-15` border | `animateOff('contact')` |

Label below each button, 11px/500.

#### Swipe action → API calls

| Action | API calls | Stage transition |
|---|---|---|
| **like** (Xem thêm) | `POST /api/v1/campaigns/{id}/research` body: `{ listing_ids: [listing.id] }` | `new` → `researching` (done by backend). Response includes `research_ids: string[]` — use the first ID to optimistically set `listing.research_id` in `useListingStore` before the next fetch. The backend sets `research_id` on the listing synchronously, so a re-fetch will also have it. |
| **contact** (Liên hệ luôn) | `PATCH /api/v1/campaigns/{id}/listings/{listing.id}` body: `{ stage: "contacted" }` | `new` → `contacted` |
| **skip** (Bỏ qua) | `PATCH /api/v1/campaigns/{id}/listings/{listing.id}` body: `{ stage: "skipped", skip_reason: "other" }` | `new` → `skipped` |

Fire-and-forget — do not await before animating the card off. Optimistic UI. Revert on error (use `sonner` toast).

---

### 5.3 Track Screen (`track-screen.tsx`)

**Layout:** `flex-col, h-full, overflow-y-auto, bg: --cream, pb: 32px`

Sticky header at top (`position: sticky, top: 0, z: 20, bg: --cream, border-bottom: 1px solid --ink-04`):
- Title: "Đang theo dõi" — 22px/800
- Subtitle: `"{total} căn đang trong quá trình"` — 13px, `--ink-50`

#### Sections

Three sections rendered in order:

**Section 1 — Đang xem xét** (`--amber` dot)
Listings with `stage = "researching"`

**Section 2 — Đã liên hệ** (`--terra` dot)
Listings with `stage = "contacted"` or `stage = "viewing"`

**Section 3 — Xong** (`--ink-30` dot)
Listings with `stage = "viewed"` or `stage = "shortlisted"`
Collapsed by default if count > 0 (show "X căn đã xem" toggle). Hidden if count = 0.

Each section has a header row: colored dot + section name (left) / count (right). Sections with 0 listings are hidden entirely.

#### TrackCard

Horizontal layout, `bg: --white, border: 1px solid --ink-08, border-radius: 20px, padding: 12px`:

```
[78×78 thumbnail]  [content flex-col]  [right col]
                   ├── name (13px/600, truncated)
                   ├── price (13px/700, --terra)
                   └── chips: bedrooms, area_sqm, district
```

**Right column (stacked top/bottom):**
- Top: `ResearchBadge` (see below)
- Bottom: timestamp "X phút trước" or source platform label

**ResearchBadge** — shown on every TrackCard:

| Listing state | Badge | Colors |
|---|---|---|
| `stage=researching`, research status `queued` or `running` | Pulsing dot + "Khảo sát" | `--amber-15` bg, `--amber` text |
| `stage=researching`, research status `done` | "★ {score}" | `--jade-15` bg, `--jade` text |
| `stage=researching`, research status `failed` | "Lỗi" | `#fde8e8` bg, `#c03` text |
| `stage=contacted` or `viewing` | "Đã liên hệ" | `--terra-15` bg, `--terra` text |

To get research status: join `listing.research_id` → `useResearchStore`. The backend sets `research_id` on the listing **synchronously** in the same transaction as the research record creation. After the swipe-right POST returns, update `listing.research_id` optimistically using the `research_ids[0]` from the response. If `research_id` is still null (race condition on first render), show pulsing badge — it will resolve on the next store sync.

**Real-time updates:** subscribe to the research SSE stream (`GET /api/v1/campaigns/{id}/research/stream`) via the existing `useResearchStream` hook. When a `completed` event fires for a research record, update the matching listing's badge in the store.

Tapping a TrackCard → open listing detail (Phase 2 — for now, no-op or show a `sonner` toast "Chi tiết sắp ra mắt").

#### TrackCard — field mapping

| Field | Shown? | Where / How |
|---|---|---|
| `title` | ✅ | **"name" line** — `listing.title`, 13px/600, `--ink`, single line truncated with ellipsis. This is the scraper-extracted listing headline (e.g. "Cho thuê phòng trọ quận 3, nội thất đầy đủ"). **Null fallback:** show `listing.address ?? "Tin đăng"`. |
| `price_display` | ✅ | Price chip — 13px/700, `--terra`. |
| `bedrooms` | ✅ | Chip — `"{bedrooms}PN"`. Hide if null. |
| `area_sqm` | ✅ | Chip — `"{area_sqm}m²"`. Hide if null. |
| `district` | ✅ | Chip — district text. Hide if null. |
| `thumbnail_url` | ✅ | 78×78 thumbnail, `border-radius: 14px`, `object-fit: cover`. **Null fallback:** same warm gradient as Discover card (`linear-gradient(135deg, #E8DDD1, #C4B5A5)`). |
| `updated_at` | ✅ | Timestamp — **use `listing.updated_at`** (reflects the most recent stage change). Format: relative time "X phút trước" / "X giờ trước" / "X ngày trước". This is more meaningful than `created_at` because it shows recency of action, not when first scraped. |
| `source_platform` | ✅ | Shown in timestamp row as suffix when `updated_at` is older than 24h: `"batdongsan · 3 ngày trước"`. When fresh (< 24h) the source is omitted — timestamp alone is enough. |
| `research_id` + `useResearchStore` | ✅ | Drives `ResearchBadge` state — see badge table above. `research_id` is set synchronously on the backend at swipe-right time. |
| `match_score` | ❌ | **Not shown on TrackCard.** User already decided to research this listing (swipe-right was the intent signal). `match_score` is a pre-decision signal — it's no longer relevant here. `overall_score` from research replaces it once research completes. |
| `address` | ❌ | Not shown — district chip is enough at list density. Available in Phase 2 detail. |
| `posted_date` | ❌ | Not shown — `updated_at` is more relevant at this stage. |
| `description` | ❌ | Not shown. Phase 2 detail screen. |
| `landlord_*` fields | ❌ | Not shown on list card. Phase 2 detail screen (and outreach flow). |
| `deposit_vnd`, `price_vnd` | ❌ | Use `price_display` (formatted). Deposit shown in Phase 2 detail. |
| `listing_url`, `skip_reason`, `user_notes` | ❌ | Phase 2 detail / notes panel. |

---

### 5.4 Settings Screen (`settings-screen.tsx`)

**Layout:** `flex-col, overflow-y-auto, bg: --cream, pb: 40px`

Header: "Cài đặt" — 22px/800, padding `16px 20px 20px`

#### Section: Tìm kiếm hiện tại

`SearchQueryCard` — shows active campaign preferences as pills:
- district, bedrooms as "XPN", price as "≤ Xtr", area as "≥ Xm²"
- "Sửa" button (right) — links to campaign edit (Phase 2: no-op)

#### Section: Kết nối

Settings group (white card, `--r-lg`, `--ink-08` border):

| Row | Icon bg | Label | Sub | Right |
|---|---|---|---|---|
| Zalo | `#e6f4ec` 💬 | Zalo | "Nhắn tin và nhận thông báo" | Status chip (Đã kết nối / Chưa kết nối) + chevron |
| Facebook | `#e8edf8` 📘 | Facebook | "Quét tin từ các nhóm cho thuê" | Status chip + chevron |
| BDS.com.vn | `#f5ece8` 🏠 | BDS.com.vn | "Nguồn tin chính" | "Hoạt động" chip + chevron |

Status chip colors: connected → `--jade-15` bg / `--jade` text. Disconnected → `--ink-08` bg / `--ink-30` text.

Tap Zalo row → wire to existing Zalo auth flow from `useZaloStore`. Tap FB/BDS → no-op for now (Phase 2).

#### Section: Lịch hoạt động

Settings group:

| Row | Label | Sub | Right |
|---|---|---|---|
| Auto scan | Quét tự động | "Mỗi 2 giờ · 7:00 – 22:00" | Toggle (on by default) |
| New listing notif | Thông báo tin mới | "Ngay khi có tin phù hợp" | Toggle (on) |

Toggles: read from `campaign.scan_frequency`. On toggle: `PATCH /api/v1/campaigns/{id}` with `{ scan_frequency: "auto" | "manual" }`.

#### Section: Phân tích khu vực

| Row | Label | Sub | Right |
|---|---|---|---|
| Auto research | Khảo sát tự động | "Khi bạn nhấn 'Xem thêm'" | Toggle (always on, non-interactive — this is a product decision, not a setting) |
| Research notif | Thông báo kết quả khảo sát | "Khi phân tích khu vực xong" | Toggle (on) |

---

## 5.5 Live Preview (Tinyfish Agent Browser)

Both the scan runner and research runner broadcast a live browser URL via SSE. The frontend embeds this URL in an `<iframe>` so the user can watch the agent work in real-time. This is an existing capability — it just needs to be placed correctly in the new mobile layout.

### Data sources

| Flow | SSE event field | Store |
|---|---|---|
| Scan | `ScanSSEEvent.streaming_url` | `useScanStreamStore` → `streamingUrls: Record<string, string>` |
| Research | `ResearchSSEEvent.browser_url` | `useResearchStore` → per-research `liveState.browserUrl` |

### Scan live preview — placement in Discover Screen

**Trigger:** User taps a "Quét ngay" button (add to `DiscoverHeader` right side when no scan is running — replace the filter icon placeholder).

**While scan is running:**

1. `DiscoverHeader` shows a pulsing amber indicator: `"● Đang quét..."` in place of the count badge
2. Tapping the indicator opens the **ScanLiveSheet** (bottom sheet, slides up over the card stack)

**ScanLiveSheet layout:**
```
Bottom sheet, max-height: 85vh, border-radius: 24px 24px 0 0
├── Handle bar (drag to dismiss)
├── Header row: "Đang tìm căn hộ..." + dismiss (×) button
├── iframe (flex: 1, min-height: 300px)
│     src = useScanStreamStore.streamingUrls[activeUrl] ?? ""
│     sandbox="allow-scripts allow-same-origin"
│     Fallback: skeleton if no URL yet
└── Progress footer: "{completedUrls}/{totalUrls} trang · {listingsFound} tin tìm thấy"
```

**On scan complete:**
- Sheet auto-dismisses
- Header badge updates to `"{newListings} mới"` with `--terra` background
- `sonner` toast: `"Quét xong · {newListings} tin mới"` with a ✓ icon

**Constraint:** Only 1 scan can run per campaign. If scan is running when user opens Discover tab, restore the amber indicator and sheet availability.

---

### Research live preview — placement in Track Screen

**Trigger:** Each `TrackCard` with `stage=researching` and research `status=running` shows a `"Xem trực tiếp →"` text button below the pulsing "Khảo sát" badge.

Tapping it opens the **ResearchLiveSheet** for that specific listing's research job.

**ResearchLiveSheet layout:**
```
Bottom sheet, max-height: 90vh, border-radius: 24px 24px 0 0
├── Handle bar
├── Header: listing name (truncated) + "Đang khảo sát khu vực" + dismiss (×)
├── iframe (height: 55% of sheet)
│     src = research.liveState.browserUrl ?? ""
│     Fallback: skeleton with "Đang khởi động..." if no URL yet
└── Step log (remaining height, overflow-y: scroll)
      Scrolling list of research steps from ResearchSSEEvent.step / .detail
      Most recent step pinned to bottom (auto-scroll)
```

**Multiple simultaneous:** Each TrackCard manages its own sheet independently. Tapping "Xem trực tiếp →" on a second card opens a new sheet stacked above the first (or replaces it — acceptable either way).

**On research complete:** Sheet can remain open showing the final state. Score arc appears in the iframe area is replaced by the final `overall_score` displayed large with verdict text. The "Xem trực tiếp →" button on the TrackCard is replaced by `"★ {score}"` badge.

---

### TrackCard update (add to section 5.3)

When `stage=researching` and research `status=running`, the TrackCard right column becomes:

```
[right col — updated]
├── ResearchBadge (pulsing amber "Khảo sát")
├── "Xem trực tiếp →"   ← NEW, 11px/500, --terra color
└── timestamp
```

---

## 6. Backend Changes

**Minimal.** All required endpoints already exist.

| Need | Existing endpoint | Notes |
|---|---|---|
| Fetch new listings | `GET /api/v1/campaigns/{id}/listings?stage=new` | Add `limit=10` query param client-side |
| Fetch tracked listings | `GET /api/v1/campaigns/{id}/listings` (no stage filter) | Filter client-side by stages shown in Theo dõi |
| Swipe right (trigger research) | `POST /api/v1/campaigns/{id}/research` body `{ listing_ids, criteria }` | Already moves listing to `researching` |
| Swipe contact (skip research) | `PATCH /api/v1/campaigns/{id}/listings/{id}` body `{ stage: "contacted" }` | Unchanged |
| Swipe skip | `PATCH /api/v1/campaigns/{id}/listings/{id}` body `{ stage: "skipped", skip_reason: "other" }` | Unchanged |
| Research status stream | `GET /api/v1/campaigns/{id}/research/stream` (SSE) | Use existing `useResearchStream` hook |
| Zalo status | `GET /api/v1/zalo/status` | Use existing `useZaloStore` |

**One small addition recommended:** Add `?limit=10&offset=0` support to `GET /listings` endpoint for paginating the discover feed. Current endpoint returns all listings which is fine for now — add pagination only if performance becomes an issue.

---

## 7. Out of Scope (Phase 2)

Do not build these now. Stub them with comments or no-ops.

| Feature | Why deferred |
|---|---|
| Listing detail / research breakdown screen | Needs new screen design, not blocking launch |
| Onboarding changes | Current SetupWizard works |
| Push notifications | Requires native wrapper |
| Skip reason selector | "other" is fine as default skip reason |
| Card detail on tap in Theo dõi | "Sắp ra mắt" toast is acceptable |
| Campaign edit from search pill | Non-blocking |
| Facebook / BDS connection flows | Zalo is the priority |
| Multiple campaigns | Existing flow works |
| Chat / AI assistant | Existing, don't break it |

---

## 8. Build Sequence

Ordered for fastest working product. Each step should leave the app in a shippable state.

### Step 1 — App shell + bottom nav (~30 min)

Create `src/components/app/app.tsx`. Wire to `page.tsx` replacing `<Dashboard>`. Render three placeholder screens with `<div>Khám phá</div>` etc. Bottom nav switches between them. **Ship check:** app loads, tabs switch.

### Step 2 — Discover screen: static card (~1.5 hrs)

Build `RentalCard`, `CardStack`, `DiscoverHeader`, `ActionBar` with hardcoded data. No gestures yet. Match the prototype visual exactly — use tokens from `system.md`. **Ship check:** card stack renders correctly at 390px.

### Step 3 — Discover screen: wire data (~1 hr)

Connect `useListingStore` — fetch listings with `stage=new`. Render real listing data in cards. Show real count in header badge.

### Step 4 — Discover screen: swipe gestures + actions (~1.5 hrs)

Add pointer event drag logic to `RentalCard`. Wire three action buttons. Call correct APIs on action. Optimistic UI — remove card immediately, toast on error. **Ship check:** can swipe through 5 cards, each triggers correct API.

### Step 5 — Track screen (~2 hrs)

Build `TrackScreen`, `TrackSection`, `TrackCard`. Fetch all non-new/non-skipped listings. Connect `useResearchStore` for badge states. Subscribe to research SSE stream for live updates. **Ship check:** researching listings show pulsing badge; completed research shows score.

### Step 6 — Settings screen (~1 hr)

Build `SettingsScreen`. Wire Zalo connection status from `useZaloStore`. Wire scan frequency toggle. Rest is display-only. **Ship check:** settings renders, Zalo shows real connection state.

### Step 6b — Live preview sheets (~1.5 hrs)

Build reusable `BottomSheet` wrapper component. Build `ScanLiveSheet` — wire to `useScanStreamStore.streamingUrls` and progress fields. Add "Quét ngay" button + amber scan indicator to `DiscoverHeader`. Build `ResearchLiveSheet` — wire to `useResearchStream` live state per research ID. Add "Xem trực tiếp →" tap target to `TrackCard` when research is running. **Ship check:** tapping "Quét ngay" shows sheet with iframe; tapping "Xem trực tiếp" on a researching card shows that research's browser.

### Step 7 — Polish + mobile viewport (~30 min)

- Add `viewport` meta tag if not present: `width=device-width, initial-scale=1`
- Test at 390px (iPhone 14 viewport) in Chrome DevTools
- Check thumb reach — all actions should be reachable with one thumb
- Verify `sonner` toasts don't overlap bottom nav

### Step 8 — Smoke test full flow (~30 min)

Run full user journey:
1. Open app → Khám phá loads with new listings
2. Swipe right on 2 cards → both move to Theo dõi with pulsing badges
3. Swipe bolt on 1 card → moves to Đã liên hệ in Theo dõi immediately
4. Swipe skip on 1 card → disappears from feed
5. Wait for research to complete → badge updates to score
6. Navigate to Cài đặt → Zalo status accurate

---

## Appendix: Key File Paths

```
Frontend root:    examples/rentagent_vn/frontend/
Design system:    .interface-design/system.md
Prototype HTML:   redesign-concept.html
API types:        frontend/src/types/index.ts
Existing stores:  frontend/src/stores/
API client:       frontend/src/lib/api.ts
Research hook:    frontend/src/hooks/use-research-stream.ts
```

## Appendix: CSS Token Quick Reference

See `.interface-design/system.md` for full reference. Most-used tokens:

```css
--cream: #FAF7F2       /* base bg */
--white: #FFFFFF       /* elevated cards */
--terra: #C4562A       /* primary accent, like button */
--jade:  #3D7A63       /* research done */
--amber: #B87B2A       /* research running */
--ink:   #1A1815       /* primary text */
--ink-50: rgba(26,24,21,.50)
--ink-15: rgba(26,24,21,.15)   /* borders */
--ink-08: rgba(26,24,21,.08)   /* card borders */

--r-xl:   26px   /* main cards */
--r-lg:   20px   /* track cards, settings groups */
--r-full: 999px  /* pills, badges */
```
