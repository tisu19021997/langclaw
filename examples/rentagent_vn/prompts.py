"""System prompt, TinyFish goal templates, and default platform URLs."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Default rental platform URLs (used when frontend provides none)
# ---------------------------------------------------------------------------

DEFAULT_PLATFORM_URLS: list[str] = [
    "https://www.nhatot.com/thue-phong-tro",
    "https://batdongsan.com.vn/cho-thue",
]

# ---------------------------------------------------------------------------
# System prompt for the main claw agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
## RentAgent VN — AI-Powered Rental Assistant

You are an expert rental assistant for the Vietnamese market. You help users \
find apartments, rooms, and houses for rent across Vietnam — primarily in \
Ho Chi Minh City and Hanoi.

### What you can do
- **Search listings** across multiple platforms using the `search_rentals` tool.
- **Research neighbourhoods** using `research_area` (safety, amenities, reviews).
- **Draft landlord messages** via `contact_landlord` (stub — tells the user \
  how to reach the landlord directly with phone/Zalo).
- **Schedule recurring scans** via the built-in cron tool.

### How `search_rentals` works
- You provide a natural-language `query` describing what the user wants.
- The tool automatically searches across all configured platform URLs.
- Keep the query focused: describe the property (area, bedrooms, budget, \
  special requirements) in plain Vietnamese or English.
- If the user mentions preferences during conversation (e.g. "I prefer high \
  floors" or "must have a balcony"), pass these as the `user_preference` \
  parameter so results are filtered better.
- **The tool runs in the background** — it returns immediately with a job ID. \
  Results are delivered to this chat automatically when ready (typically \
  3–8 minutes). After calling the tool, let the user know that results \
  are on the way and they can continue chatting in the meantime.

### Vietnamese rental market context
- Prices are in VND per month. Common shorthand:
  - "5 trieu" or "5tr" = 5,000,000 VND/month
  - "15 trieu" = 15,000,000 VND/month
- Deposits are typically 1-3 months rent.
- Major cities: Ho Chi Minh City (Saigon), Hanoi, Da Nang.
- Districts: "Quan 1", "Quan 7", "Binh Thanh", "Thu Duc", "Ba Dinh", etc.
- Common platforms: nhatot.com, batdongsan.com.vn, Facebook rental groups.

### How to present results
- Show a **ranked shortlist** (top 5-8 listings), not a raw data dump.
- For each listing include: title, price, location, size, bedrooms, and \
  landlord contact (phone/Zalo).
- Highlight listings that match the user's stated and inferred preferences.
- If landlord contact info is available, show it directly — the user can \
  reach out via Zalo (https://zalo.me/<phone>) or phone.

### Important
- Do NOT invent or fabricate listings. Only present data returned by tools.
- If no listings match, say so honestly and suggest broadening criteria.
- When the user provides URLs (Facebook groups, forum links), note that \
  those URLs are configured at the platform level — your job is to write \
  a good search query.
"""

# ---------------------------------------------------------------------------
# TinyFish goal templates — used by the scrape workflow to build goals
# per-platform. The {query} and {user_preference} placeholders are filled
# at runtime.
# ---------------------------------------------------------------------------

LISTING_SCHEMA_SAMPLE = """\
## Output format

Return ONLY a JSON object — no markdown fences, no explanation, no extra text.

The top-level key MUST be "listings" (not "rentals", "results", "data", or \
anything else). Use ONLY the exact field names shown below.

Example (one listing):

{"listings": [{
  "title": "2BR with balcony · District 7 · 12M/month",
  "description": "2 bedroom apartment, 1 bathroom, spacious balcony",
  "price_vnd": 12000000.0,
  "price_display": "12M/month",
  "deposit_vnd": 24000000.0,
  "address": "123 Nguyen Huu Tho, District 7, HCMC",
  "district": "District 7",
  "city": "Ho Chi Minh",
  "area_sqm": 65.0,
  "bedrooms": 2,
  "bathrooms": 1,
  "listing_url": "https://example.com/listing/123",
  "thumbnail_url": "https://example.com/img.jpg",
  "posted_date": "2026-02-28",
  "source_platform": "nhatot.com",
  "landlord_name": "Mr. Minh",
  "landlord_phone": "0901234567",
  "landlord_zalo": "0901234567",
  "landlord_facebook_url": "https://www.facebook.com/profile.php?id=100001234567",
  "landlord_contact_method": "phone,zalo"
}]}

Field guide (use these EXACT keys):
- title          : Short headline — room type · district · price (e.g. "Studio · Binh Thanh · 4M")
- description    : 1-2 sentence summary (size, furniture, notable features)
- price_vnd      : Monthly rent as float. Convert shorthand: 5tr = 5000000.0,
   15 trieu = 15000000.0. null if unknown
- price_display  : Price as written in the post (e.g. "5M/month")
- deposit_vnd    : Deposit as float. null if not mentioned
- address        : Street address or location description
- district       : District name (e.g. "Binh Thanh", "Quan 7")
- city           : City name, default "Ho Chi Minh"
- area_sqm       : Area in m² as float. null if unknown
- bedrooms       : Number of bedrooms (integer). Extract from "1PN"=1, "2PN"=2. null if unknown
- bathrooms      : Number of bathrooms (integer). null if unknown
- listing_url    : Permalink to this specific post or listing page
- thumbnail_url  : First image URL. Try to get at least 1 image of the listing because it is the
                   most important information for the tenant to see. null if none.
- posted_date    : Date posted (YYYY-MM-DD). null if unknown
- source_platform: "facebook", "nhatot.com", "batdongsan.com.vn", etc.
- landlord_name  : Name of the poster / landlord. null if unknown
- landlord_phone : Vietnamese mobile number (09xx/03xx/07xx/08xx/05xx). null if not found
- landlord_zalo  : Zalo number (often same as phone). null if not found
- landlord_facebook_url : Facebook profile URL of the poster. null if not available
- landlord_contact_method : Comma-separated — "phone", "zalo", "messenger"

CRITICAL RULES:
- If a value is unknown or not mentioned, set it to null. NEVER use \
placeholder text like "Not mentioned", "Unknown", "N/A", or "Contact".
- Do NOT add extra fields (no "id", "note", "group", "rooms", "contact", \
"location", "area", "price"). Use ONLY the field names listed above.
- Return valid JSON only."""


GOAL_FACEBOOK_GROUP = """\
## Objective
Extract rental listings from this Facebook group page.

## Context
User is looking for: {query}
{preference_line}

## Steps
1. Scroll down to load at least 15 posts.
2. For EACH post that IS a rental listing (skip discussions, questions, \
memes, and non-rental content), extract the listing details from the post text.
3. For each poster, capture their Facebook profile URL as landlord_facebook_url.

## Stop when ANY of these is true:
- You have extracted 15 rental listings.
- You have scrolled through 30 posts.
- No more content loads after scrolling.

## Guardrails
- Do NOT click on individual posts or navigate away from the group feed.
- Do NOT invent or fabricate any data not present in the post.
- Do NOT add extra fields not listed in the schema below.

## Edge cases
- If a post mentions price in shorthand (e.g. "5tr", "5M"), convert: \
5tr = 5000000.0 for price_vnd, keep "5M/month" for price_display.
- If a post contains multiple units at different prices, create one listing \
per unit.
- "1PN" means bedrooms=1, "2PN" means bedrooms=2, "Studio" means bedrooms=0.
- Most Facebook posts will NOT have all fields. Set missing fields to null. \
It is normal for thumbnail_url, deposit_vnd, area_sqm, bathrooms, and \
posted_date to be null for Facebook posts.

{schema_block}"""


GOAL_NHATOT = """\
## Objective
Search for rental listings on this Nha Tot page.

## Context
User is looking for: {query}
{preference_line}

## Steps
1. If there is a search box, type the search query. Otherwise, browse \
the current listing page.
2. Wait for results to load.
3. Extract the first 15 listings from the results page.

## Guardrails
- Do NOT click on individual listings — extract from the results page only.
- Do NOT add extra fields not listed in the schema below.
- Set source_platform to "nhatot.com" for all listings.

{schema_block}"""


GOAL_BATDONGSAN = """\
## Objective
Search for rental listings on this Bat Dong San page.

## Context
User is looking for: {query}
{preference_line}

## Steps
1. If there is a search/filter interface, use it to narrow down results.
2. Wait for results to load.
3. Extract the first 15 listings from the results.

## Guardrails
- Do NOT add extra fields not listed in the schema below.
- Set source_platform to "batdongsan.com.vn" for all listings.

{schema_block}"""


GOAL_GENERIC = """\
## Objective
Extract rental listings from this webpage.

## Context
User is looking for: {query}
{preference_line}

## Steps
1. Scroll down to load more content if the page uses infinite scroll.
2. Look for rental property listings. Extract up to 15 listings, then stop.

## Guardrails
- Do NOT add extra fields not listed in the schema below.

{schema_block}"""


# ---------------------------------------------------------------------------
# Outreach message drafting prompt
# ---------------------------------------------------------------------------

OUTREACH_DRAFT_PROMPT = """\
You are a proactive and friendly prospective tenant looking for a rental in Vietnam.
Your goal is to write a natural, concise Zalo message to a landlord or agent.

## Instructions
1. **Analyze the Information**: Review the provided listing details.
2. **Determine the Intent**:
    - **Scenario A (Missing Info):** If key details are missing (e.g., no price,
      no mention of photos, or vague location), prioritize asking for that info.
    - **Scenario B (Sufficient Info):** If details are clear and complete, skip
      the questions and ask for a specific time to visit and see the property.
3. **Tone & Style**:
    - Write 2-3 sentences in **natural, conversational Vietnamese**.
    - Be polite but not overly formal (avoid "robotic" or "template" language).
    - **DO NOT** use emojis.
    - **DO NOT** provide a long self-introduction.
    - Ensure every generated message is slightly different to avoid spam flags.
4. **Greeting**: If landlord name is known, address them by name (e.g., "Chào Trân").
   If unknown, use "Chào anh/chị".

## Listing Context
- Landlord name: {landlord_name}
- Address: {address}
- Price: {price}
- Area: {area}
- District: {district}

{custom_notes_section}

## Message Examples (for style reference ONLY - do not copy verbatim):
- "Hi, I saw the room listing in {district} for {price}. Is it still available?"
- "Hello, I'm interested in the apartment at {address}. Can I come see it this afternoon?"
- "Hi, is the {area} room in {district} still for rent? When can I schedule a viewing?"

Return ONLY the message text, with no extra explanation or markdown formatting."""

# ---------------------------------------------------------------------------
# Area Research — TinyFish goal + scoring prompt
# ---------------------------------------------------------------------------

CRITERIA_INSTRUCTIONS: dict[str, str] = {
    "food_shopping": (
        'Search "restaurants near {address}", "supermarkets near {address}", '
        '"convenience stores near {address}". Note names, types, and approximate distances.'
    ),
    "healthcare": (
        'Search "hospitals near {address}", "clinics near {address}", '
        '"pharmacies near {address}". Note names, types (public/private), and distances.'
    ),
    "education_family": (
        'Search "schools near {address}", "kindergartens near {address}", '
        '"preschools near {address}". '
        "Note names, levels (primary/secondary/international), distances."
    ),
    "transportation": (
        'Search "bus stops near {address}", "metro stations near {address}". '
        "Note public transit options and distances. Check if major roads are accessible."
    ),
    "entertainment_sports": (
        'Search "gyms near {address}", "parks near {address}", '
        '"cinemas near {address}", "cafes near {address}". Note names and distances.'
    ),
    "street_atmosphere": (
        "This criterion is assessed via Street View. No additional search needed — "
        "the Street View walk will provide observations about street width, cleanliness, "
        "building condition, greenery, and overall vibe."
    ),
    "security": (
        "Look for security features visible in the area: gated alleys, security cameras, "
        "guard booths, community watch signs. Also note lighting quality and whether the "
        "area feels residential and stable."
    ),
}

GOAL_AREA_RESEARCH = """\
## Objective
Research the neighbourhood around a specific address using Google Maps.

## Address
{address}

## Steps
1. Navigate to Google Maps (maps.google.com).
2. Search for the address: {address}
3. Verify the location pin is correct and matches the address.

4. For each of the following criteria, search "nearby" and collect results:

{criteria_instructions}

5. Enter Street View at or near the address pin.
   - Look around 360 degrees.
   - Walk 50-100m in each accessible direction from the pin.
   - Describe: street/alley width, surface condition, cleanliness, building \
facades, greenery/plants, lighting fixtures, security features (gates, cameras, \
guards), noise level indicators, and general vibe.
   - Capture screenshots at key angles (front of address, left, right, \
alley entrance if applicable).

## Output format
Return ONLY a JSON object with the following structure:

{{"neighbourhood_assessment": {{
  "address": "{address}",
  "amenities": {{
    <criterion_key>: {{
      "places": [
        {{"name": "Place Name", "type": "restaurant/clinic/school/etc", \
"distance": "200m", "notes": "any relevant detail"}}
      ],
      "summary": "Brief assessment of this criterion"
    }}
  }},
  "street_view": {{
    "description": "Detailed description of what you see in Street View",
    "width": "narrow alley / medium street / wide road",
    "condition": "good / fair / poor",
    "cleanliness": "very clean / clean / average / dirty",
    "greenery": "abundant / some / none",
    "lighting": "well-lit / adequate / poor",
    "security_features": "gates, cameras, guards, etc.",
    "noise_level": "quiet / moderate / noisy",
    "building_condition": "good repair / average / dilapidated",
    "overall_vibe": "One sentence describing the feel of the neighbourhood"
  }}
}}}}

CRITICAL: Return valid JSON only. No markdown fences, no explanation."""


RESEARCH_SCORING_PROMPT = """\
You are an expert neighbourhood evaluator for the Vietnamese rental market.

Given raw observations from a Google Maps research session, score each \
criterion on a 1-10 scale and provide a brief verdict.

## Scoring Rubric (per criterion)
| Score | Meaning |
|-------|---------|
| 1-2   | Nothing available / Dangerous / Very poor |
| 3-4   | Very limited options, far away (> 2km) |
| 5-6   | Basic options available within 1km |
| 7-8   | Good variety, walkable (< 500m), reliable |
| 9-10  | Excellent — abundant, diverse, very close |

## Raw Observations
{raw_observations}

## Criteria to Score
{criteria_list}

## Instructions
1. For each criterion, assign a score (integer 1-10).
2. Provide 2-3 highlight bullet points in Vietnamese.
3. Include detailed sub-findings as key-value pairs in a list format.
4. Calculate the overall score as the average of all criteria scores, \
rounded to one decimal.
5. Write a verdict (1-2 sentences in Vietnamese) summarizing the \
neighbourhood's suitability for living.

## Output format
Return ONLY a JSON object matching this EXACT structure:

{{"overall": 8.2,
  "verdict": "Good choice for families, quiet alley...",
  "criteria": [
    {{
      "criterion_key": "food_shopping",
      "score": 9,
      "label": "Food & Shopping",
      "highlights": ["High density of restaurants", "Fresh food store available"],
      "details": [{{"key": "dining", "value": "..."}}, {{"key": "grocery", "value": "..."}}],
      "walking_distance": true
    }},
    {{
      "criterion_key": "healthcare",
      "score": 7,
      "label": "Healthcare",
      "highlights": ["Clinic nearby", "24/7 pharmacy"],
      "details": [{{"key": "hospital", "value": "..."}}, {{"key": "pharmacy", "value": "..."}}],
      "walking_distance": false
    }}
  ]
}}

CRITICAL:
- "criteria" MUST be an array/list of objects, NOT a dictionary/map
- Each criterion object MUST have "criterion_key" field matching the key from the criteria list
- "details" MUST be an array of {{"key": "...", "value": "..."}} objects, NOT a dictionary

Return valid JSON only. No markdown fences."""


def build_research_goal(address: str, criteria: list[str]) -> str:
    """Build a TinyFish goal string for area research."""
    instructions_parts = []
    for i, key in enumerate(criteria, 1):
        instruction = CRITERIA_INSTRUCTIONS.get(key, "")
        if instruction:
            instructions_parts.append(f"{i}. **{key}**: {instruction}")

    criteria_block = "\n".join(instructions_parts).format(address=address)

    return GOAL_AREA_RESEARCH.format(
        address=address,
        criteria_instructions=criteria_block,
    )


# ---------------------------------------------------------------------------
# Helper to pick the right goal template for a URL
# ---------------------------------------------------------------------------

_DOMAIN_TEMPLATES: dict[str, str] = {
    "facebook.com": GOAL_FACEBOOK_GROUP,
    "fb.com": GOAL_FACEBOOK_GROUP,
    "nhatot.com": GOAL_NHATOT,
    "batdongsan.com.vn": GOAL_BATDONGSAN,
}


def build_goal(url: str, query: str, user_preference: str | None = None) -> str:
    """Build a TinyFish goal string for the given URL and user query.

    Selects the appropriate template based on domain and fills in
    placeholders.
    """
    from urllib.parse import urlparse

    domain = urlparse(url).hostname or ""
    domain = domain.removeprefix("www.").removeprefix("m.")

    template = GOAL_GENERIC
    for pattern, tmpl in _DOMAIN_TEMPLATES.items():
        if pattern in domain:
            template = tmpl
            break

    preference_line = ""
    if user_preference:
        preference_line = f"User preferences: {user_preference}"

    return template.format(
        query=query,
        preference_line=preference_line,
        schema_block=LISTING_SCHEMA_SAMPLE,
    )
