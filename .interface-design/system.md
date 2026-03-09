# Interface Design System — Nhà Tốt Redesign

## Product Intent

**Who:** Vietnamese Gen Z / late millennials (born 1990s–2000s), apartment hunting in HCMC. On their phone in the evening, decompressing. Used to Zalo, TikTok, Shopee UX patterns — comfortable with dense UI but love gesture-based, image-first flows.

**What they must do:** Decide quickly on apartments. The core verb is *decide* — then *track*.

**Feel:** Like a friend texting you apartment recommendations. Warm, fast, low-cognitive-load. Not a real estate tool. Not admin work. Confident and unhurried.

---

## Domain

Concepts pulled from HCMC urban life:
- Hẻm (alley intimacy), ánh sáng buổi chiều (afternoon light), cửa sổ (window as first judgment)
- Khu vực (neighborhood as identity), số tháng (monthly framing of cost)
- Chủ nhà / môi giới (human at the other end), ban công (coveted feature), khảo sát (research/due diligence)

---

## Color Palette

| Token | Value | Source |
|-------|-------|--------|
| `--cream` | `#FAF7F2` | Sun-faded HCMC concrete — app base |
| `--cream-100` | `#F3EDE2` | Slightly deeper surface |
| `--cream-200` | `#E8E0D0` | Dividers, skeleton states |
| `--ink` | `#1A1815` | Evening pavement — primary text |
| `--ink-70` | `rgba(26,24,21,.70)` | Secondary text |
| `--ink-50` | `rgba(26,24,21,.50)` | Supporting text |
| `--ink-30` | `rgba(26,24,21,.30)` | Muted / labels |
| `--ink-15` | `rgba(26,24,21,.15)` | Borders |
| `--ink-08` | `rgba(26,24,21,.08)` | Subtle borders |
| `--ink-04` | `rgba(26,24,21,.04)` | Hover fills, dividers |
| `--terra` | `#C4562A` | Terracotta roof tiles — primary accent |
| `--terra-15` | `rgba(196,86,42,.15)` | Tinted backgrounds |
| `--terra-08` | `rgba(196,86,42,.08)` | Very subtle tints |
| `--jade` | `#3D7A63` | Painted shutters — research done / success |
| `--jade-15` | `rgba(61,122,99,.15)` | |
| `--amber` | `#B87B2A` | Afternoon light — research running / warning |
| `--amber-15` | `rgba(184,123,42,.15)` | |
| `--white` | `#FFFFFF` | Elevated card surfaces |

**Rule:** Same hue, shift only lightness. Never different hue for different surfaces. Color carries meaning — terra = action, jade = done/success, amber = in-progress.

---

## Typography

Font: **Inter** (Google Fonts, Vietnamese subset)

| Role | Size | Weight | Letter-spacing | Notes |
|------|------|--------|----------------|-------|
| Price / Hero | 28–30px | 900 | -1.2px | Rent IS the headline |
| Screen title | 22px | 800 | -0.8px | |
| Section name | 13px | 600 | -0.2px | |
| Body / card name | 13–15px | 500–600 | -0.3px | |
| Meta / chips | 11–12px | 500–600 | -0.1px | |
| Labels (uppercase) | 11px | 600 | +0.8px | Section headers in settings |

---

## Depth Strategy: Subtle Shadows

Single approach throughout — no mixing.

```css
--shadow-card:  0 2px 8px rgba(26,24,21,.06), 0 8px 32px rgba(26,24,21,.10);
--shadow-float: 0 4px 24px rgba(26,24,21,.12), 0 12px 48px rgba(26,24,21,.12);
```

- **Base:** `--cream` (#FAF7F2)
- **Elevated cards:** `--white` (#FFFFFF) with `1px solid var(--ink-08)` border
- **Card stack (discovery):** shadow-float — cards need presence to feel swipeable
- No dramatic shadows, no thick borders, no pure black

---

## Border Radius Scale

| Token | Value | Usage |
|-------|-------|-------|
| `--r-xs` | 6px | Tiny elements |
| `--r-sm` | 10px | Inputs, small buttons |
| `--r-md` | 14px | Thumbnails, chips |
| `--r-lg` | 20px | Cards in tracking / settings groups |
| `--r-xl` | 26px | Main discovery cards |
| `--r-full` | 999px | Pills, badges, toggles |

Rounder feels friendly — appropriate for a consumer mobile app used in relaxed evening context.

---

## Spacing

Base unit: **4px**

| Scale | Value | Usage |
|-------|-------|-------|
| s1 | 4px | Icon internal gaps |
| s2 | 8px | Chip internal padding, tight gaps |
| s3 | 12px | Card internal padding |
| s4 | 16px | Standard padding, item rows |
| s5 | 20px | Screen horizontal margins |
| s6 | 24px | Card body padding |
| s8 | 32px | Section separation |
| s10 | 40px | Bottom padding on scrollable screens |

---

## Navigation

**Bottom tab bar — 3 tabs:**
- Khám phá (heart icon) — discovery feed
- Theo dõi (grid icon) — tracking / management
- Cài đặt (gear icon) — settings

Tab bar: `--cream` background, `1px solid var(--ink-08)` top border, 80px height (accounts for home indicator). Active state: `--terra` color for icon and label.

**No sidebar.** Mobile-first. All navigation thumb-reachable.

---

## Signature Element: Khảo Sát Arc

The research score displayed as a circular arc badge in the top-right corner of discovery card images. **Only appears when research is complete** — absent cards are unresearched.

```html
<div class="score-arc"> <!-- 54×54px, position: absolute top-right -->
  <svg viewBox="0 0 54 54" fill="none">
    <circle cx="27" cy="27" r="19" fill="rgba(0,0,0,.35)"/>        <!-- dark bg for legibility -->
    <circle cx="27" cy="27" r="19" class="arc-ring-bg"             <!-- track ring, low opacity -->
      stroke-dasharray="119.4"/>
    <circle cx="27" cy="27" r="19" class="arc-ring hi|md"          <!-- progress ring -->
      stroke-dasharray="119.4"
      stroke-dashoffset="[119.4 * (1 - score/10)]"/>
    <text x="27" y="30" class="arc-num">8.1</text>
  </svg>
</div>
```

**Arc math:** Circumference = 2π × 19 = 119.4px. Offset = 119.4 × (1 − score/10).

**Color coding:**
- Score ≥ 8.0 → `stroke: #57d99a` (class: `hi`)
- Score < 8.0 → `stroke: #f0b860` (class: `md`)
- Ring rotated −90° so arc starts at 12 o'clock

---

## Discovery Card Pattern

Full-bleed image card with gradient overlay. Image dominates — 100% of card surface.

```
[Full-bleed image via background-image]
[Gradient overlay: transparent → rgba(14,12,10,.82) at bottom]
[Source tag: top-left, frosted glass pill]
[Score arc: top-right, only if researched]
[Swipe indicators: sw-like right / sw-skip left, opacity driven by drag delta]
[Card body: fixed bottom, price (900 weight) + specs chips + location]
```

**Card stack transforms (3 visible):**
- Top card: `z-index: 3`, no transform
- Middle card: `z-index: 2`, `translateY(12px) scale(.955)`
- Back card: `z-index: 1`, `translateY(24px) scale(.91)`

**Three actions (left→right):**
1. Skip (54px white circle, X icon) — animate card left
2. Like / Xem thêm (64px terracotta circle, heart icon) — primary, animate card right, triggers research
3. Contact / Liên hệ luôn (46px white circle, bolt icon) — skip research, direct outreach

**Swipe threshold:** 85px horizontal displacement → animate off screen.

---

## Tracking Card Pattern

Horizontal layout: image thumbnail left, content right, status badge + time far right.

```
[78×78px thumbnail, --r-md border-radius]
[Content: name (13px/600), price (13px/700/terra), chips row]
[Right: research badge stacked above timestamp]
```

**Research status badges:**

| State | Class | Colors |
|-------|-------|--------|
| Research running | `rbadge running` | amber bg/text + pulse dot |
| Research done | `rbadge done` | jade bg/text + ★ score |
| Contacted | `rbadge reached` | terra bg/text |
| Awaiting reply | `rbadge pending` | ink-08 bg / ink-50 text |

**Pulse animation for running state:**
```css
@keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
.pulse { animation: blink 1.5s ease-in-out infinite; }
```

---

## Settings Pattern

**Groups:** `--white` background, `--r-lg` radius, `1px solid var(--ink-08)` border, `0 var(--s4)` horizontal margin. Items separated by `1px solid var(--ink-04)` bottom border (last item: none).

**Item anatomy:** 36×36 icon container (colored bg, `--r-sm`) + label/sub stack + right-side control.

**Section labels:** 11px, 600 weight, uppercase, `letter-spacing: .8px`, `--ink-30` color.

**Toggle switch:** 44×26px, `--r-full`. Off: `--ink-15` bg. On: `--terra` bg. Knob: 20×20 white circle, `transform: translateX(18px)` when on.

**Status chips:**
- Connected: `--jade-15` bg, `--jade` text
- Disconnected: `--ink-08` bg, `--ink-30` text

**Search query card:** `--white` bg, `--r-lg`, `--ink-08` border, flex row with wrap tags + edit button. Query tags: `--terra-08` bg, `--terra` text.

---

## Screens

| Screen | ID | Scroll | Layout |
|--------|----|--------|--------|
| Khám phá | `s-discover` | No (overflow hidden) | Flex column: header + stack area (flex:1) + actions |
| Theo dõi | `s-track` | Yes | Sticky header + sections with track-list |
| Cài đặt | `s-settings` | Yes | Sections with settings groups |

**Screen switching:** `position: absolute; inset: 0; opacity: 0; pointer-events: none` → `.active` sets opacity 1. Transition: `opacity .2s ease`.

---

## Decisions Made (Don't Revisit)

- **Warm cream base, not white** — differentiates from every other real estate tool
- **Three-action pattern** (skip / like+research / contact-now) — not two. The bolt shortcut is essential for power users
- **Research triggers on swipe right, NOT on discovery** — cost and UX reasons; notification re-engagement is valuable
- **5–10 listings/day limit** — keeps the queue manageable, Tinder pattern works at this volume
- **No sidebar navigation** — mobile-first, bottom tabs only
- **Score arc only on researched cards** — absence communicates pending status without a badge
- **Kanban replaced** by 3-section flat list: Đang xem xét / Đã liên hệ / Xong
