# Nova Global Tracking — Failure Transcript (2026-07-20)

Attempted to track `NP80000004830699NPG`. Every approach failed. This reference catalogs all dead ends so future sessions don't repeat them.

## Endpoints Tried (all failed)

### nova.global / novaposhtaglobal.ua
| URL | Result |
|-----|--------|
| `nova.global/track/` (web_extract) | DuckDuckGo extract backend — "search-only backend, cannot extract URL content" |
| `novaposhtaglobal.ua/uk/track/` (curl HTML) | React SPA shell — `<div id="root"></div>`, no tracking data |
| `api.novaposhtaglobal.ua/v1/tracking/NP...` | Empty response |
| `api.novaposhtaglobal.ua/api/tracking/NP...` | Empty response |
| `api.novaposhtaglobal.ua/api/track/NP...` | Empty response |
| `api.novaposhtaglobal.ua/v1/track/NP...` | Empty response |
| `api.novaposhtaglobal.ua/api/public/tracking/NP...` | Empty response |
| `personal.novaposhtaglobal.ua/api/tracking/NP...` | Empty response |
| `personal.novaposhtaglobal.ua/api/track/NP...` | Empty response |
| `personal.novaposhtaglobal.ua/tracking/NP...` | Empty response |
| `nova.global/tracking.php?Tracking_ID=NP...` | Empty response |
| `novaposhtaglobal.ua/tracking.php?Tracking_ID=NP...` | Empty response |
| `nova.global/track/?Tracking_ID=NP...` | General WordPress page, no tracking data |
| `novaposhtaglobal.ua/uk/track/?Tracking_ID=NP...` | Empty (SPA) |
| `api.nova.global/v1/tracking/NP...` | 302 redirect (nginx) |

### Third-party trackers
| Service | Attempt | Result |
|---------|---------|--------|
| **Nova Poshta domestic API** | `api.novaposhta.ua/v2.0/json/` → `TrackingDocument.getStatusDocuments` | Error: "Document number is not correct" — international format rejected |
| **track.global** | `/en/tracking/NP...` (HTML) | SPA, no embedded data |
| **track.global** | `/en/tracking/search` (POST) | No response |
| **track.global** | `/en/api/v1/tracking/NP...` | 404/empty |
| **track.global** | `/en/api/track/search?numbers=NP...` | Empty |
| **track.global** | `/api/v1/search?q=NP...` | Empty |
| **17track.net** | `api.17track.net/track/v2.2/gettrackinfo` (POST) | 401 — "Access token is invalid" |
| **parcelsapp.com** | `api.parcelsapp.com/v3/shipments/NP...?apiKey=...` | Failed (requires valid API key) |
| **gdeposylka.ru** | `/courier/novaposhta-int/tracking/NP...` | SPA, curl times out |
| **gdeposylka.ru** | `/api/tracking/NP...` | Empty |

### Search-based
| Query | Result |
|-------|--------|
| `"NP80000004830699NPG" site:nova.global` | No tracking results — only generic tracking page |
| `"NP80000004830699NPG" nova.global tracking` | Same |
| `"NP80000004830699NPG" parcelsapp OR track.global` | No results |
| `NP80000004830699NPG трекинг Нова Глобал` | No specific tracking data |
| `"NP80000004830699NPG" tracking status 2026` | No results (Google/Bing don't index individual parcel pages) |
| `novaposhtaglobal.ua api tracking endpoint "NP800000"` | No API docs found |
| Perplexity: "Nova Poshta Global tracking NP80000004830699NPG last status Ukraine Kyiv" | Cannot retrieve real-time status — only generic instructions on how to track |

## Additional Attempts (2026-07-20, build ID `SJ5ctaVHKmdP7heRniwDq`)

| Method | Result |
|--------|--------|
| `nova.global/_next/data/<buildId>/ua-ua/track.json?Tracking_ID=NP...` | Returns full page JSON, but **only homepage content** — tracking data is NOT in `__NEXT_DATA__` or `pageProps`. Page is `__N_SSG: true` (static generation). |
| `nova.global/ua-ua/track/` → `__NEXT_DATA__` extraction | Same — only static homepage data, zero tracking data server-side. |
| Perplexity (cron with perplexity skill): `"nova.global tracking NP80000004830699NPG status"` | Returns only generic tracking instructions, no live data. |
| `track.global/api/v1/tracking?number=NP...&carrier=nova-poshta-global` | 404 |
| `parcelsapp.com/api/v3/shipments` (POST) | `Cannot POST /api/v3/shipments` |
| `personal.novaposhtaglobal.ua/wp-json/` | No tracking-related namespaces |
| `personal.novaposhtaglobal.ua/wp-admin/admin-ajax.php?action=np_track` | Empty |

## Key Takeaway
Nova Global international tracking is a **closed system**. The tracking page is a Next.js SSG SPA (`__N_SSG: true`)— no tracking data is server-rendered in HTML, `__NEXT_DATA__`, or `_next/data` JSON endpoints. Tracking data loads exclusively client-side via an internal API (likely with reCAPTCHA v3: `6LelNNghAAAAAP8mdcIC_XUzSQBSK8e1tlonP65-`). There is no public REST API. Every path leads to either an empty response, a redirect, or an SPA shell with no server-rendered data.

The only working approach: open `https://nova.global/ua-ua/track/?Tracking_ID=NP...` in a real browser (JavaScript + captcha), or have the user send screenshots.
The only working approach: open `https://nova.global/track/` in a real browser, solve the captcha, and enter the tracking number manually.
