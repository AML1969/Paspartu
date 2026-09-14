---
name: parcel-tracking
description: Track parcels from international carriers programmatically — which carriers have accessible APIs, which are SPA/JS-only, and proven approaches for each.
tags: [tracking, parcels, logistics, api, carriers]
---

# Parcel Tracking — Programmatic Access

## Trigger
Use when asked to check tracking status of any parcel (Nova Poshta, Nova Global, UPS, FedEx, DHL, etc.) via cron or autonomous session.

## Carrier Access Matrix

### ✅ Accessible (public API, no auth needed)
| Carrier | Method | Notes |
|---------|--------|-------|
| **Nova Poshta (domestic)** | `api.novaposhta.ua/v2.0/json/` — `TrackingDocument.getStatusDocuments` | Domestic TTN only (14 digits). No API key needed for tracking. |

### ⛔ Inaccessible (captcha or auth required)
| Carrier | Reason | Notes |
|---------|--------|-------|
| **17track.net** | API requires access token (401 without it). | |
| **parcelsapp.com** | API requires apiKey. Public page is JS-rendered. | |
| **track.global** | React SPA. API endpoints not publicly routed. | |
| **gdeposylka.ru** | JS-rendered page. No public API found. | |

### ✅ Accessible without a browser (direct API)
| Carrier | Method | Notes |
|---------|--------|-------|
| **Nova Global (international)** | Direct API (curl) → `scripts/np-track.js` | Tracking numbers: `NP...NPG`. Endpoint `personal.novaposhtaglobal.ua/tracking.php` returns the full status history as JSON. No browser, no cookie banner. <1 sec per check. |

## Nova Global — Direct API (✅ Works, since 20.07.2026)

Tracking numbers like `NP80000004830699NPG` are checked through the carrier's own endpoint —
no browser at all. `nova.global` is a React SPA, but `personal.novaposhtaglobal.ua/tracking.php`
accepts a plain multipart POST and answers with JSON containing the whole status history.

**Script:** `/root/hermes-workspace/scripts/np-track.js`

```bash
node /root/hermes-workspace/scripts/np-track.js NP80000004830699NPG
```

**How it works:**
1. POSTs `num=НОМЕР&lang=ua` to `https://personal.novaposhtaglobal.ua/tracking.php` via curl (25s timeout).
2. Parses the JSON answer — field `historyStatus` holds the full timeline.
3. No browser is started: no Chromium, no cookie banner, no rendering wait.
5. Compares latest status with saved state in `/root/hermes-workspace/.np-track-НОМЕР.json`.
6. Outputs JSON with `latest`, `previous`, `changed`, `all_statuses`.
7. Prints `=== STATUS_CHANGED ===` when status differs from previous run.

**Cron setup:**
```bash
cronjob create \
  --name "Nova Poshta tracking NP..." \
  --schedule "every 1h" \
  --prompt "Run: node /root/hermes-workspace/scripts/np-track.js NP.... If STATUS_CHANGED in output — alert user with new status. Otherwise STAY SILENT." \
  --enabled_toolsets '["terminal"]' \
  --deliver origin
```

**⚠️ Note (reversed on 20.07.2026).** This skill used to say «curl always fails, go straight to
Playwright» — that was true only for the *public page* `nova.global`, which is client-side rendered.
The carrier's own endpoint `personal.novaposhtaglobal.ua/tracking.php` does answer curl. Do NOT
start a browser for Nova Global, and do NOT install one. `references/nova-global-failures.md`
still lists the approaches that genuinely fail (17track, parcelsapp, web_search and friends).

## Nova Global / NP Shopping — Support & Escalation Contacts

When a parcel is stuck (e.g. customs >2 days with no movement), contact NP Shopping support. If support ignores you, escalate to group management.

### Primary support
- **Email:** `support@novaposhtaglobal.ua`
- **Send from:** paspartu.amlai@gmail.com (google-workspace)
- **Tracking notifications come from:** `noreply@npshopping.com` (do not reply to these)
- **Live chat:** available on nova.global — user may prefer this over email and get faster responses

### Escalation contacts (when support ignores — all verified working, 31.07.2026)
Full details: `references/nova-global-escalation.md`

### ⛔ CEO emails — DO NOT guess (will bounce)
CEO email format `name.surname@domain` does **not** work:
- `yuriy.benevitskiy@novaposhtaglobal.ua` — 550 address not found (31.07.2026)
- `yevhen.tafiychuk@novaposhta.ua` — address not found (31.07.2026)

Use `correspondence@novaposhta.ua` instead — it reaches the CEO office through the official reception channel.

### Mass escalation pattern (when parcel stuck 5+ days)
1. Send to `support@novaposhtaglobal.ua` as primary recipient
2. CC all escalation addresses (`info@`, `correspondence@`, `corp.com@`, `office@`)
3. Frame complaint as **Nova Poshta failing their paid brokering service**, NOT as a complaint against customs
4. Set deadline: "if no answer by Monday — official complaint"

### Standard workflow
1. Check current status via `scripts/np-track.js`
2. Draft email in Ukrainian with tracking number, current status, and specific questions
3. Send via `google_api.py gmail send --to support@novaposhtaglobal.ua --subject "..." --body "..."`
4. **Record the action in hmem** — update the parcel entity with the support inquiry date and threadId
5. Check for replies periodically (check personal Gmail too — NP sometimes replies to buyer's email)

**⚠️ After getting a support answer (email or chat), update hmem immediately** so that no further automated support inquiries are sent. The tracking cron must check hmem for existing support contact before sending anything.

**⚠️ Pitfall — `gmail reply` fails when original message had no recipient.** If a previous support email was sent without `--to` (empty recipient — a known google-workspace pitfall), `gmail reply` to that message ID will fail with `HTTP 400: Recipient address required`. Workaround: use `gmail send --to support@novaposhtaglobal.ua` with a new explicit recipient. This starts a new thread instead of continuing the broken one.

**⚠️ Pitfall — verify the email was actually sent.** After gmail send, check the returned JSON has `status: "sent"` AND a valid `threadId`. Never assume it went through.

**⚠️ Pitfall — NEVER send support emails from a tracking cron.** Cron jobs should ONLY check tracking status and report changes. If the user already contacted support (via email or chat), the cron MUST NOT send another support inquiry. Before sending any support email, check hmem for a prior support contact record.

**⚠️ Pitfall — bounced escalation addresses.** After a mass send, check for Mail Delivery Subsystem bounces within a few minutes. Resend to any failed addresses using alternatives from the escalation table. Don't silently accept unreachable recipients.

## Nova Global — Customs & Route Knowledge

When predicting delivery times, use the domain knowledge in `references/nova-global-customs.md`:

- **Route:** France → Italy → Czechia → Poland → Ukraine (Kyiv)
- **EU export customs:** Poland (hours, not days)
- **Ukrainian customs:** NOT at the border — at the Kyiv Central Sorting Station (ЦСС, Салютная 2А). The parcel travels under customs control from Poland directly to Kyiv. Takes hours, not days.
- **Total from Czechia departure to Kyiv branch:** 1–2 days. Do NOT add extra days for "border customs" — it doesn't exist as a separate step.

User corrections encoded here:
- Customs never takes 2 days («там никогда не бывает таможни двое суток»)
- Ukrainian customs is at the Nova Poshta terminal in Kyiv, not at the border
- Don't skip the Kyiv sorting center in timeline predictions

## Nova Poshta Domestic — API (current status only)

Public API without key. The 14-digit domestic TNN is often linked to an international `NP...NPG` track via the `ClientBarcode` field.

```bash
curl -s "https://api.novaposhta.ua/v2.0/json/" \
  -H "Content-Type: application/json" \
  -d '{"apiKey":"","modelName":"TrackingDocument","calledMethod":"getStatusDocuments","methodProperties":{"Documents":[{"DocumentNumber":"20400048799000"}]}}'
```

**⚠️ LIMITATION: returns ONLY current status, NOT history.** Key fields: `StatusCode`, `Status`, `ScheduledDeliveryDate`, `WarehouseRecipient`, `ClientBarcode`, `DocumentCost`, `FactualWeight`, `DateScan`. For full history with past scans and planned future stops, see next section.

## Nova Poshta Domestic — Playwright for full history (✅ Works)

When the user needs the full tracking timeline (past scans + planned stops), the public API is insufficient. Scrape `tracking.novaposhta.ua` via Playwright:

```js
const { chromium } = require('playwright');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('https://tracking.novaposhta.ua/#/uk', { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForTimeout(3000);

// Enter tracking number
const input = await page.locator('input').first();
await input.fill('20700712954793');
await input.press('Enter');
await page.waitForTimeout(4000);

// Dismiss the first-visit popup («Добре»)
const popupBtn = page.locator('button:has-text("Добре")').first();
if (await popupBtn.count() > 0) {
  await popupBtn.click({ force: true });
  await page.waitForTimeout(1000);
}

// Click «Детальніше» to expand full history
const detailBtn = page.locator('span:has-text("Детальніше")').first();
await detailBtn.click({ force: true });
await page.waitForTimeout(3000);

const text = await page.textContent('body');
await browser.close();
```

**Pitfalls:**
- The first-visit helper popup (`first-visit-helper-wrapper`) blocks clicks — dismiss it with `{ force: true }` on the «Добре» button before clicking «Детальніше»
- The page is a Vue SPA with `#/uk` hash routing — `networkidle` often times out, use `domcontentloaded` + manual waits instead
- Planned future stops appear with `check_box_outline_blank` icons and include time predictions

## Cron Tracking — Decision Tree

1. Identify carrier from tracking number format:
   - `NP...NPG` → Nova Global → **direct API** (`scripts/np-track.js`, curl под капотом)
   - 14-digit number → Nova Poshta domestic → **API** (curl)
   - Other formats → Perplexity search for carrier, then decide

2. **Nova Global:** use the direct-API script. Set cron to `every 1h` when parcel is near Ukraine border, `every 4h` otherwise.

3. **API-accessible carriers:** query API, compare with last known status.

4. **Recording:** save each tracked parcel as an entity in hmem with tracking URL, cron job_id, and last known status.

## Legacy: Why Nova Global was marked ⛔

Before the carrier endpoint was found (20.07.2026), every approach failed (naive curl of the public page, API probes, Perplexity, web_search, third-party trackers), and the skill relied on Playwright. The full failure catalog is preserved in `references/nova-global-failures.md` — 10 distinct failed approaches. This is kept for reference: if Playwright is ever unavailable, don't waste time retrying these.
