"""
VW Used Cars Scraper
Scrapes Volkswagen UK certified used car listings (ID.4 & ID.5)
and sends results via email.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


SEARCH_URL = (
    "https://usedcars.volkswagen.co.uk/en/vehicle_search/volkswagen?"
    "POOLS_CSV=47-12532-217084&RADIUS_LEN_FLT=900"
    "&PRICE_RETAIL_CUR_FLT_TO=30000&MILEAGE_MIL_INT_TO=20000"
    "&INITIAL_REGISTRATION_DTE_FROM=2024&search=passenger"
    "&MANUFACTURER_LST=VOLKSWAGEN&priceSwitch=on"
    "&MODEL_TYPE_LST=VOLKSWAGEN_ID_4||VOLKSWAGEN_ID_5"
    "&ZIP_LOC=SW20%209DQ&sort=PRICE_RETAIL_CUR_FLT:ASC"
)

OUTPUT_FILE = "listings.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# JavaScript injected before every page to hide headless/automation signals.
STEALTH_JS = """
// Remove navigator.webdriver flag
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// Spoof Chrome runtime
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };

// Fake permissions API
const origQuery = window.navigator.permissions?.query?.bind(window.navigator.permissions);
if (origQuery) {
  window.navigator.permissions.query = (params) =>
    params.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : origQuery(params);
}

// Spoof plugins (headless has 0)
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});

// Spoof languages
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-GB', 'en-US', 'en'],
});
"""


def scrape_listings() -> list[dict]:
    """Scrape VW used car listings using headless browser with network interception."""
    # Primary strategy: headless browser with network interception
    print("[Strategy 1] Headless browser with network interception...")
    listings = _scrape_via_browser()
    if not listings:
        # Fallback: requests + BeautifulSoup (works if site returns server-rendered HTML)
        print("[Strategy 2] Attempting requests + HTML parsing...")
        listings = _scrape_via_requests()

    # Log raw extraction results before filtering
    if listings:
        print(f"\n  === RAW EXTRACTION RESULTS ({len(listings)} listings) ===")
        for i, listing in enumerate(listings[:3]):
            print(f"  Listing {i+1}: {json.dumps(listing, indent=2)}")
        if len(listings) > 3:
            print(f"  ... and {len(listings) - 3} more")
        print(f"  === END RAW RESULTS ===\n")

    # Post-extraction: filter out non-vehicle entries
    before = len(listings)
    listings = _filter_valid_listings(listings)
    after = len(listings)
    if before != after:
        print(f"  Filtered out {before - after} non-vehicle entries ({before} -> {after})")

    return listings


def _scrape_via_browser() -> list[dict]:
    """Scrape using Playwright headless Chromium with network interception."""
    if sync_playwright is None:
        print("  Playwright not installed — skipping browser strategy.")
        return []

    listings = []
    captured_api_responses = []

    def handle_response(response):
        """Capture JSON responses that may contain vehicle data."""
        try:
            content_type = response.headers.get("content-type", "")
            url = response.url
            if response.status == 200 and "json" in content_type:
                body = response.json()
                captured_api_responses.append({
                    "url": url,
                    "data": body,
                })
        except Exception:
            pass

    try:
        with sync_playwright() as p:
            # Use non-headless if DISPLAY is set (xvfb in CI), else new headless
            use_headless = os.environ.get("DISPLAY") is None
            print(f"  Headless mode: {use_headless}")
            browser = p.chromium.launch(
                headless=use_headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-GB",
                timezone_id="Europe/London",
                geolocation={"latitude": 51.4214, "longitude": -0.2064},
                permissions=["geolocation"],
            )
            # Inject stealth scripts before any page loads
            context.add_init_script(STEALTH_JS)
            page = context.new_page()

            # Intercept network responses to capture API data
            page.on("response", handle_response)

            # Navigate to the home page first, then the search — mimics real user
            print("  Visiting home page first...")
            page.goto(
                "https://usedcars.volkswagen.co.uk/en/home",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            _dismiss_cookie_banner(page)
            page.wait_for_timeout(2000)

            print(f"  Navigating to search: {SEARCH_URL[:80]}...")
            page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)

            # Accept cookies again if banner reappears
            _dismiss_cookie_banner(page)

            # Wait for dynamic content to fully load
            page.wait_for_timeout(5000)

            # Scroll slowly like a human to trigger lazy loading
            for _ in range(5):
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(1500)

            # Extra wait for any final API calls after scrolling
            page.wait_for_timeout(2000)

            # === Extraction Phase ===

            # Step 1: Check captured API responses for vehicle data
            print(f"  Captured {len(captured_api_responses)} JSON API responses.")
            for resp_data in captured_api_responses:
                api_url = resp_data["url"]
                data = resp_data["data"]
                # Log the first few API responses in detail for debugging
                _log_api_response(api_url, data)
                # Try structured parsing first (handles VW-specific field names)
                vehicles = _parse_api_response(data)
                if not vehicles:
                    vehicles = _find_vehicles_in_obj(data)
                if vehicles:
                    print(f"  Found {len(vehicles)} vehicles from API: {api_url[:120]}")
                    # Log the first extracted vehicle for debugging
                    if vehicles:
                        print(f"  Sample extracted vehicle: {json.dumps(vehicles[0], indent=2)}")
                    listings.extend(vehicles)

            if listings:
                print(f"  Total from API interception: {len(listings)}")
                _save_debug_artifacts(page)
                browser.close()
                return _deduplicate(listings)

            # Step 2: Try extracting from embedded scripts / JSON-LD
            print("  No vehicles from API calls. Trying embedded data...")
            listings = _extract_from_page_scripts(page)
            if listings:
                print(f"  Found {len(listings)} from embedded scripts.")
                _save_debug_artifacts(page)
                browser.close()
                return listings

            # Step 3: Smart vehicle-link extraction (walks from <a> tags to card containers)
            print("  Trying vehicle-link extraction...")
            link_listings = _extract_from_vehicle_links(page)
            if link_listings:
                print(f"  Found {len(link_listings)} from vehicle-link extraction.")
                if _has_price_data(link_listings):
                    _save_debug_artifacts(page)
                    browser.close()
                    return link_listings
                print("  Vehicle-link results lack valid prices, continuing...")

            # Step 4: Try DOM-based extraction with many selectors
            print("  Trying DOM-based extraction...")
            dom_listings = _extract_from_dom(page)
            if dom_listings:
                print(f"  Found {len(dom_listings)} from DOM extraction.")
                if _has_price_data(dom_listings):
                    _save_debug_artifacts(page)
                    browser.close()
                    return dom_listings
                print("  DOM results lack valid prices, trying text extraction...")

            # Step 5: Extract from visible text (proximity-based parsing)
            print("  Trying text-based extraction...")
            text_listings = _extract_from_page_text(page)
            if text_listings:
                print(f"  Found {len(text_listings)} from text extraction.")
                if _has_price_data(text_listings):
                    _save_debug_artifacts(page)
                    browser.close()
                    return text_listings

            # Step 6: Use whichever results are richer
            all_candidates = [
                ("vehicle-link", link_listings or []),
                ("text", text_listings or []),
                ("dom", dom_listings or []),
            ]
            # Pick the candidate set with the most valid prices
            best_name, listings = max(
                all_candidates,
                key=lambda x: sum(1 for l in x[1] if l.get("price")),
            )
            if listings:
                print(f"  Using best available: {best_name} ({len(listings)} listings)")

            # Always save debug artifacts for CI inspection
            _save_debug_artifacts(page)
            _save_api_debug(captured_api_responses)
            _log_page_diagnostics(page, captured_api_responses)

            print(f"  Extracted {len(listings)} listings total.")
            browser.close()
    except Exception as e:
        print(f"  Browser scraping failed: {e}")

    return listings


def _dismiss_cookie_banner(page) -> None:
    """Try to dismiss cookie consent banners."""
    cookie_selectors = [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
        "button:has-text('Accept All Cookies')",
        "button:has-text('Allow all')",
        "button:has-text('Allow All')",
        "button:has-text('Agree')",
        "button:has-text('OK')",
        "[data-testid='cookie-accept']",
        ".cookie-accept",
        "#accept-cookies",
        "button[class*='consent']",
        "button[class*='cookie']",
    ]
    for selector in cookie_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click()
                print(f"  Accepted cookies via: {selector}")
                page.wait_for_timeout(2000)
                return
        except Exception:
            continue
    print("  No cookie banner found or already dismissed.")


def _extract_from_dom(page) -> list[dict]:
    """Try to extract vehicle listings from the DOM using various selectors."""
    listings = []

    # Try many possible card selectors
    card_selectors = [
        "[data-testid='vehicle-card']",
        "[data-testid*='vehicle']",
        "[data-testid*='car']",
        "[data-testid*='listing']",
        "[data-testid*='result']",
        ".vehicle-card",
        ".result-list-entry",
        ".car-card",
        ".listing-item",
        "article.vehicle",
        "[class*='VehicleCard']",
        "[class*='vehicleCard']",
        "[class*='vehicle-card']",
        "[class*='ResultList'] > div",
        "[class*='resultList'] > div",
        "[class*='SearchResult']",
        "[class*='searchResult']",
        ".sc-vehicle-tile",
        "[class*='offer-card']",
        "[class*='OfferCard']",
        "[class*='vehicle-tile']",
        "[class*='VehicleTile']",
        "[class*='car-tile']",
        "[class*='CarTile']",
        "a[href*='/vehicle_search/volkswagen/id-']",
        "a[href*='/vehicle_search/volkswagen/id.']",
    ]

    for selector in card_selectors:
        try:
            found = page.locator(selector)
            count = found.count()
            if count > 0:
                print(f"  Found {count} elements with selector: {selector}")
                # Log first card's HTML and text for debugging
                try:
                    first_html = found.nth(0).evaluate("el => el.outerHTML")
                    print(f"  First card HTML (truncated): {first_html[:1000]}")
                    first_text = found.nth(0).inner_text()
                    print(f"  First card text: {first_text[:500]}")
                except Exception:
                    pass
                for i in range(min(count, 50)):
                    card = found.nth(i)
                    listing = _extract_card_data(card)
                    if listing and (listing.get("title") or listing.get("raw_text")):
                        listings.append(listing)
                if listings:
                    # If no listing has price, cards may be too small — try parents
                    if not any(l.get("price") for l in listings):
                        print("  Cards lack price data, trying parent elements...")
                        parent_listings = _try_parent_extraction(found, count)
                        if parent_listings and any(l.get("price") for l in parent_listings):
                            return parent_listings
                    return listings
        except Exception:
            continue

    return listings


def _extract_card_data(card) -> dict:
    """Extract data from a single vehicle card element."""
    data = {}

    # Title
    for sel in ["h2", "h3", "h4", ".title", "[class*='title']", "[class*='Title']",
                "[class*='name']", "[class*='Name']", "[class*='model']", "[class*='Model']"]:
        try:
            el = card.locator(sel).first
            if el.is_visible(timeout=300):
                text = el.inner_text().strip()
                if text and len(text) > 3:
                    data["title"] = text
                    break
        except Exception:
            continue

    # Price
    for sel in ["[class*='price']", "[class*='Price']", "[data-testid*='price']", ".price"]:
        try:
            el = card.locator(sel).first
            if el.is_visible(timeout=300):
                text = el.inner_text().strip()
                if "\u00a3" in text or "GBP" in text or re.search(r'\d', text):
                    data["price"] = text
                    break
        except Exception:
            continue

    # Mileage
    for sel in ["[class*='mileage']", "[class*='Mileage']", "[class*='kilometer']",
                "[class*='Kilometer']", "[class*='odometer']"]:
        try:
            el = card.locator(sel).first
            if el.is_visible(timeout=300):
                data["mileage"] = el.inner_text().strip()
                break
        except Exception:
            continue

    # Year / Registration
    for sel in ["[class*='year']", "[class*='Year']", "[class*='registration']",
                "[class*='Registration']", "[class*='date']"]:
        try:
            el = card.locator(sel).first
            if el.is_visible(timeout=300):
                text = el.inner_text().strip()
                if re.search(r'20[12]\d', text):
                    data["year"] = text
                    break
        except Exception:
            continue

    # Link — check if card itself is an <a>, then check descendants, then parent
    try:
        card_href = card.evaluate(
            "el => el.tagName === 'A' ? (el.getAttribute('href') || '') : ''"
        )
        if card_href:
            if card_href.startswith("/"):
                card_href = "https://usedcars.volkswagen.co.uk" + card_href
            data["url"] = card_href
        else:
            link = card.locator("a[href]").first
            href = link.get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = "https://usedcars.volkswagen.co.uk" + href
                data["url"] = href
    except Exception:
        pass
    # If still no URL, check if card is inside a link (parent <a>)
    if not data.get("url"):
        try:
            parent_href = card.evaluate("""el => {
                let p = el.parentElement;
                for (let i = 0; i < 5 && p; i++) {
                    if (p.tagName === 'A' && p.href) return p.getAttribute('href');
                    p = p.parentElement;
                }
                return '';
            }""")
            if parent_href:
                if parent_href.startswith("/"):
                    parent_href = "https://usedcars.volkswagen.co.uk" + parent_href
                data["url"] = parent_href
        except Exception:
            pass

    # Fallback: parse missing fields from the card's visible text
    try:
        full_text = card.inner_text().strip()
        if full_text:
            if not data.get("title"):
                data["raw_text"] = full_text[:500]
                title_match = re.search(
                    r'((?:Volkswagen|VW)\s+ID[.\s]?[345]\S*(?:\s+\S+){0,8})',
                    full_text, re.IGNORECASE,
                )
                if title_match:
                    data["title"] = title_match.group(1).strip()
            if not data.get("price"):
                price_match = re.search(r'£[\d,]+', full_text)
                if price_match:
                    data["price"] = price_match.group()
            if not data.get("year"):
                year_match = re.search(r'\b(202[0-9])\b', full_text)
                if year_match:
                    data["year"] = year_match.group(1)
            if not data.get("mileage"):
                for m in re.finditer(r'([\d,]+)\s*(?:miles|mi\b)', full_text, re.IGNORECASE):
                    ctx = full_text[max(0, m.start() - 50):m.start()].lower()
                    if any(w in ctx for w in ("annual", "expected", "excess")):
                        continue
                    data["mileage"] = m.group(1) + " miles"
                    break
    except Exception:
        pass

    return data


def _extract_from_page_scripts(page) -> list[dict]:
    """Try to extract listing data from embedded script tags / JSON."""
    listings = []

    # Check JSON-LD
    try:
        scripts = page.locator("script[type='application/ld+json']")
        for i in range(scripts.count()):
            try:
                text = scripts.nth(i).inner_text()
                obj = json.loads(text)
                items = obj if isinstance(obj, list) else [obj]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") in (
                        "Car", "Vehicle", "Product", "Offer",
                        "ItemList", "OfferCatalog",
                    ):
                        if item.get("@type") in ("ItemList", "OfferCatalog"):
                            for sub in item.get("itemListElement", []):
                                if isinstance(sub, dict):
                                    inner = sub.get("item", sub)
                                    if isinstance(inner, dict):
                                        listings.append(_normalize_ld_json(inner))
                        else:
                            listings.append(_normalize_ld_json(item))
            except Exception:
                continue
    except Exception:
        pass

    # Check window state objects
    try:
        data = page.evaluate("""() => {
            const sources = [
                window.__NEXT_DATA__,
                window.__NUXT__,
                window.__APP_STATE__,
                window.__INITIAL_STATE__,
                window.__DATA__,
                window.__PRELOADED_STATE__,
            ];
            for (const src of sources) {
                if (src) return JSON.stringify(src);
            }
            return null;
        }""")
        if data:
            parsed = json.loads(data)
            vehicles = _find_vehicles_in_obj(parsed)
            listings.extend(vehicles)
    except Exception:
        pass

    return listings


def _extract_from_page_text(page) -> list[dict]:
    """Last-resort: extract listings from visible page text and links."""
    listings = []

    # Look for links to individual vehicles (correct URL patterns)
    link_patterns = [
        "a[href*='/vehicle_search/volkswagen/id-']",
        "a[href*='/vehicle_search/volkswagen/id.']",
        "a[href*='/vehicle_detail/']",
        "a[href*='/offer']",
        "a[href*='/enquiry']",
    ]

    for pattern in link_patterns:
        try:
            all_links = page.locator(pattern)
            count = all_links.count()
            if count > 0:
                print(f"  Found {count} links matching: {pattern}")
                for i in range(min(count, 50)):
                    link = all_links.nth(i)
                    try:
                        href = link.get_attribute("href") or ""
                        text = link.inner_text().strip()
                        if href.startswith("/"):
                            href = "https://usedcars.volkswagen.co.uk" + href
                        if text and len(text) > 5:
                            listing = {"url": href, "raw_text": text[:500]}
                            # Parse structured data from text
                            price_match = re.search(r'\u00a3[\d,]+', text)
                            if price_match:
                                listing["price"] = price_match.group()
                            year_match = re.search(r'\b(202[0-9])\b', text)
                            if year_match:
                                listing["year"] = year_match.group(1)
                            for _m in re.finditer(
                                r'([\d,]+)\s*(?:miles|mi\b)',
                                text, re.IGNORECASE,
                            ):
                                _ctx = text[max(0, _m.start() - 50):_m.start()].lower()
                                if any(w in _ctx for w in ("annual", "expected", "excess")):
                                    continue
                                listing["mileage"] = _m.group(1) + " miles"
                                break
                            # Try to extract a title (model name)
                            title_match = re.search(
                                r'((?:Volkswagen|VW)\s+ID\.[45]\S*(?:\s+\S+){0,8})',
                                text, re.IGNORECASE,
                            )
                            if title_match:
                                listing["title"] = title_match.group(1).strip()
                            listings.append(listing)
                    except Exception:
                        continue
                if listings:
                    return listings
        except Exception:
            continue

    # Fallback: parse the entire body text for vehicle-like blocks
    try:
        body_text = page.inner_text("body")
        # Try proximity-based parsing first (handles varied layouts)
        listings = _parse_listings_from_text_proximity(body_text)
        if not listings:
            # Fall back to block-based parsing
            listings = _parse_listings_from_text(body_text)
    except Exception:
        pass

    return listings


def _parse_listings_from_text(text: str) -> list[dict]:
    """Parse vehicle listings from unstructured page text."""
    listings = []

    # Split text into chunks and look for vehicle-like patterns
    # Look for blocks containing both a model name and a price
    blocks = re.split(r'\n{2,}', text)
    for block in blocks:
        has_model = bool(re.search(r'ID[.\s]?[45]', block, re.IGNORECASE))
        has_price = bool(re.search(r'\u00a3[\d,]+', block))
        if has_model and has_price:
            listing = {}
            title_match = re.search(
                r'((?:Volkswagen|VW)\s+ID[.\s]?[45]\S*(?:\s+\S+){0,8})',
                block, re.IGNORECASE,
            )
            if title_match:
                listing["title"] = title_match.group(1).strip()
            price_match = re.search(r'(\u00a3[\d,]+)', block)
            if price_match:
                listing["price"] = price_match.group(1)
            for _m in re.finditer(r'([\d,]+)\s*(?:miles|mi\b)', block, re.IGNORECASE):
                _ctx = block[max(0, _m.start() - 50):_m.start()].lower()
                if any(w in _ctx for w in ("annual", "expected", "excess")):
                    continue
                listing["mileage"] = _m.group(1) + " miles"
                break
            year_match = re.search(r'\b(202[0-9])\b', block)
            if year_match:
                listing["year"] = year_match.group(1)
            if listing.get("title") or listing.get("price"):
                listing.setdefault("raw_text", block[:500])
                listings.append(listing)

    return listings


def _extract_from_vehicle_links(page) -> list[dict]:
    """Extract vehicle data by finding vehicle links and walking up to card containers."""
    try:
        results = page.evaluate("""() => {
            const selectors = [
                'a[href*="/vehicle_search/volkswagen/id-"]',
                'a[href*="/vehicle_search/volkswagen/id."]',
            ];
            let links = [];
            for (const sel of selectors) {
                links.push(...document.querySelectorAll(sel));
            }
            const vehicles = new Map();

            // Phrases that indicate finance disclosure sections
            const financeMarkers = [
                'Personal Contract Plan',
                'representative example',
                'Personalise your finance',
            ];

            function stripFinanceText(el) {
                // Build text from direct children, excluding finance sections
                let parts = [];
                for (const child of el.childNodes) {
                    const t = (child.textContent || '').trim();
                    if (!t) continue;
                    const isFinance = financeMarkers.some(m => t.includes(m));
                    if (!isFinance) {
                        parts.push(child.nodeType === 3 ? t : (child.innerText || t));
                    }
                }
                const cleaned = parts.join('\\n');
                // If stripping removed too much, fall back to full text with
                // finance sections removed via regex
                if (cleaned.length < 20) {
                    let full = el.innerText || '';
                    full = full.replace(
                        /Solutions Personal Contract Plan[\\s\\S]*?(?:per mile\\.|Personalise your finance)/gi,
                        ''
                    );
                    return full.trim();
                }
                return cleaned;
            }

            for (const link of links) {
                const href = link.getAttribute('href') || '';
                const urlPath = href.split('?')[0];
                if (vehicles.has(urlPath)) continue;

                // Walk up the DOM to find a card container with a real vehicle price
                let el = link;
                for (let i = 0; i < 10; i++) {
                    el = el.parentElement;
                    if (!el || el.tagName === 'BODY') break;
                    const fullText = el.innerText || '';
                    if (fullText.length < 30) continue;
                    if (fullText.length > 5000) break;

                    // Look for £ prices in the car price range (£5,000+)
                    const prices = fullText.match(/\\u00a3[\\d,]+/g) || [];
                    const hasCarPrice = prices.some(p => {
                        const num = parseInt(p.replace(/[\\u00a3,]/g, ''));
                        return num >= 5000 && num <= 100000;
                    });

                    if (hasCarPrice) {
                        // Use cleaned text (finance sections stripped)
                        const cleanText = stripFinanceText(el);
                        vehicles.set(urlPath, {
                            url: href.startsWith('/')
                                ? 'https://usedcars.volkswagen.co.uk' + href
                                : href,
                            text: cleanText.substring(0, 1500),
                            fullText: fullText.substring(0, 1500),
                            level: i + 1,
                        });
                        break;
                    }
                }
            }

            return [...vehicles.values()];
        }""")

        if not results:
            return []

        print(f"  Found {len(results)} vehicle card containers via link-walking")
        if results:
            first = results[0]
            print(f"  Sample at DOM level {first.get('level')}:")
            print(f"    Clean text: {first.get('text', '')[:300]}")
            print(f"    Full text:  {first.get('fullText', '')[:300]}")

        listings = []
        for item in results:
            listing = _parse_single_vehicle_text(item["text"])
            url = item.get("url", "")
            if url:
                listing["url"] = url.split("?")[0]
                if listing["url"].startswith("/"):
                    listing["url"] = "https://usedcars.volkswagen.co.uk" + listing["url"]
            if listing.get("title") or listing.get("price"):
                listings.append(listing)

        return listings
    except Exception as e:
        print(f"  Vehicle link extraction failed: {e}")
        return []


def _parse_single_vehicle_text(text: str) -> dict:
    """Parse a single vehicle's details from a text block."""
    listing = {}

    title_match = re.search(
        r'((?:Volkswagen|VW)\s+ID[.\s]?[345]\S*(?:\s+\S+){0,8})',
        text, re.IGNORECASE,
    )
    if title_match:
        listing["title"] = title_match.group(1).strip()

    price_match = re.search(r'£[\d,]+', text)
    if price_match:
        listing["price"] = price_match.group()

    year_match = re.search(r'\b(202[0-9])\b', text)
    if year_match:
        listing["year"] = year_match.group(1)

    # Find all mileage mentions, skip ones from finance disclosures
    # ("Expected / annual mileage 10,000 miles", "Excess mileage 8.24p")
    mile_matches = list(re.finditer(r'([\d,]+)\s*(?:miles|mi\b)', text, re.IGNORECASE))
    for m in mile_matches:
        context_before = text[max(0, m.start() - 50):m.start()].lower()
        if any(w in context_before for w in ("annual", "expected", "excess")):
            continue
        listing["mileage"] = m.group(1) + " miles"
        break

    url_match = re.search(r'(/en/vehicle_search/volkswagen/[^\s"\'<>]+)', text)
    if url_match:
        listing["url"] = "https://usedcars.volkswagen.co.uk" + url_match.group(1)

    listing["raw_text"] = text[:500]
    return listing


def _try_parent_extraction(found, count: int) -> list[dict]:
    """Walk up DOM tree from matched elements to find richer card containers."""
    for level in range(1, 6):
        parent_listings = []
        seen_parents = set()
        for i in range(min(count, 50)):
            try:
                parent_path = ".parentElement" * level
                parent_data = found.nth(i).evaluate(f"""el => {{
                    const parent = el{parent_path};
                    if (!parent) return null;
                    return {{
                        text: parent.innerText || '',
                        tag: parent.tagName,
                    }};
                }}""")
                if not parent_data:
                    continue
                text = parent_data["text"]
                if not text or len(text) < 20:
                    continue
                # Skip if we've already processed this parent (siblings share parents)
                text_key = text[:200]
                if text_key in seen_parents:
                    continue
                seen_parents.add(text_key)

                listing = _parse_single_vehicle_text(text)
                if listing and (listing.get("title") or listing.get("raw_text")):
                    parent_listings.append(listing)
            except Exception:
                continue
        if parent_listings and any(l.get("price") for l in parent_listings):
            print(f"  Found {len(parent_listings)} listings at parent level {level}")
            # Log a sample for debugging
            sample = next((l for l in parent_listings if l.get("price")), parent_listings[0])
            print(f"  Sample parent listing: {json.dumps(sample, indent=2)}")
            return parent_listings
    return []


def _parse_listings_from_text_proximity(text: str) -> list[dict]:
    """Parse vehicle listings using proximity of model names to prices/details."""
    listings = []

    model_pattern = r'(?:Volkswagen|VW)\s+ID[.\s]?[345]\S*'
    model_matches = list(re.finditer(model_pattern, text, re.IGNORECASE))

    if not model_matches:
        return []

    for i, match in enumerate(model_matches):
        # Window starts at this model name (not before, to avoid overlap)
        start = match.start()
        if i + 1 < len(model_matches):
            end = model_matches[i + 1].start()
        else:
            end = min(start + 1000, len(text))

        window = text[start:end]
        listing = {"title": match.group().strip()}

        price_match = re.search(r'£[\d,]+', window)
        if price_match:
            listing["price"] = price_match.group()

        year_match = re.search(r'\b(202[0-9])\b', window)
        if year_match:
            listing["year"] = year_match.group(1)

        for _m in re.finditer(r'([\d,]+)\s*(?:miles|mi\b)', window, re.IGNORECASE):
            _ctx = window[max(0, _m.start() - 50):_m.start()].lower()
            if any(w in _ctx for w in ("annual", "expected", "excess")):
                continue
            listing["mileage"] = _m.group(1) + " miles"
            break

        listing["raw_text"] = window[:500]
        listings.append(listing)

    return listings


def _has_price_data(listings: list[dict]) -> bool:
    """Check if at least some listings have valid car-range price information."""
    if not listings:
        return False
    with_valid_price = 0
    for l in listings:
        price = str(l.get("price", ""))
        price_digits = re.sub(r'[^\d]', '', price)
        if price_digits:
            try:
                price_num = int(price_digits)
                if 1000 <= price_num <= 999999:
                    with_valid_price += 1
            except (ValueError, OverflowError):
                pass
    return with_valid_price >= max(1, len(listings) * 0.3)


def _save_api_debug(api_responses: list) -> None:
    """Save all captured API responses to a debug file for inspection."""
    try:
        debug_data = []
        for resp in api_responses:
            debug_data.append({
                "url": resp["url"],
                "data_preview": json.dumps(resp["data"], indent=2, ensure_ascii=False)[:20000],
            })
        with open("debug_api_responses.json", "w", encoding="utf-8") as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)
        print(f"  Saved {len(debug_data)} API responses to debug_api_responses.json")
    except Exception as e:
        print(f"  Failed to save API debug: {e}")


def _log_api_response(url: str, data) -> None:
    """Log API response structure for debugging."""
    print(f"\n  --- API Response: {url[:150]} ---")
    if isinstance(data, dict):
        print(f"  Top-level keys: {list(data.keys())}")
        for key, val in data.items():
            if isinstance(val, list) and len(val) > 0:
                print(f"  '{key}': list of {len(val)} items")
                if isinstance(val[0], dict):
                    print(f"    First item keys: {list(val[0].keys())}")
                    # Print first item's values (truncated)
                    for k, v in val[0].items():
                        v_str = str(v)[:100]
                        print(f"      {k}: {v_str}")
            elif isinstance(val, dict):
                all_keys = list(val.keys())
                print(f"  '{key}': dict with {len(all_keys)} keys: {all_keys[:30]}")
                # Log any sub-keys that are lists (may contain vehicle data)
                for sk, sv in val.items():
                    if isinstance(sv, list) and len(sv) > 0:
                        print(f"    '{key}.{sk}': list of {len(sv)} items")
                        if isinstance(sv[0], dict):
                            print(f"      First item keys: {list(sv[0].keys())[:20]}")
            else:
                v_str = str(val)[:100]
                print(f"  '{key}': {v_str}")
    elif isinstance(data, list):
        print(f"  Top-level list of {len(data)} items")
        if data and isinstance(data[0], dict):
            print(f"    First item keys: {list(data[0].keys())}")
            for k, v in data[0].items():
                v_str = str(v)[:100]
                print(f"      {k}: {v_str}")
    print(f"  --- End API Response ---\n")


def _save_debug_artifacts(page) -> None:
    """Save page HTML and screenshot for CI debugging."""
    try:
        content = page.content()
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Saved debug HTML ({len(content)} chars)")
    except Exception as e:
        print(f"  Failed to save HTML: {e}")

    try:
        page.screenshot(path="debug_screenshot.png", full_page=True)
        print("  Saved debug screenshot")
    except Exception as e:
        print(f"  Failed to save screenshot: {e}")


def _log_page_diagnostics(page, api_responses: list) -> None:
    """Log diagnostic info about the page state for debugging."""
    try:
        print(f"\n  === DIAGNOSTICS ===")
        print(f"  Page title: {page.title()}")
        print(f"  Page URL: {page.url}")

        # Log API responses captured
        print(f"  API responses captured: {len(api_responses)}")
        for resp in api_responses:
            url = resp["url"]
            data = resp["data"]
            data_str = json.dumps(data)[:200] if isinstance(data, (dict, list)) else str(data)[:200]
            print(f"    {url[:120]}")
            print(f"      Data preview: {data_str}")

        # Log relevant text snippets
        body_text = page.inner_text("body")
        lines = body_text.split("\n")
        relevant = [
            l.strip() for l in lines
            if any(kw in l.lower() for kw in [
                "id.4", "id.5", "id 4", "id 5", "\u00a3", "price", "mileage",
                "result", "vehicle", "found", "no match",
            ])
            and l.strip()
        ]
        if relevant:
            print(f"  Relevant text snippets ({len(relevant)} found):")
            for line in relevant[:20]:
                print(f"    {line[:150]}")
        else:
            print("  No relevant text found on page.")

        # Log element counts for common patterns
        selectors_to_check = [
            "a[href*='vehicle_search']", "a[href*='vehicle_detail']",
            "a[href*='offer']", "a[href*='enquiry']",
            "[class*='vehicle']", "[class*='Vehicle']",
            "[class*='car']", "[class*='Car']",
            "[class*='result']", "[class*='Result']",
            "[class*='listing']", "[class*='Listing']",
            "[class*='card']", "[class*='Card']",
            "[class*='tile']", "[class*='Tile']",
            "[class*='offer']", "[class*='Offer']",
            "[data-testid]", "article", "li",
        ]
        print("  Element counts:")
        for sel in selectors_to_check:
            try:
                count = page.locator(sel).count()
                if count > 0:
                    print(f"    {sel}: {count}")
            except Exception:
                pass

        # Dump unique class names containing relevant keywords
        all_classes = page.evaluate("""() => {
            const classes = new Set();
            document.querySelectorAll('*').forEach(el => {
                if (el.className && typeof el.className === 'string') {
                    el.className.split(/\\s+/).forEach(c => {
                        const cl = c.toLowerCase();
                        if (cl && (cl.includes('vehicle') || cl.includes('car') ||
                                   cl.includes('result') || cl.includes('listing') ||
                                   cl.includes('card') || cl.includes('tile') ||
                                   cl.includes('offer') || cl.includes('search') ||
                                   cl.includes('srp') || cl.includes('inventory'))) {
                            classes.add(c);
                        }
                    });
                }
            });
            return [...classes].sort();
        }""")
        if all_classes:
            print(f"  Relevant CSS classes found ({len(all_classes)}):")
            for cls in all_classes[:30]:
                print(f"    .{cls}")

        print("  === END DIAGNOSTICS ===\n")
    except Exception as e:
        print(f"  Diagnostics failed: {e}")


def _scrape_via_requests() -> list[dict]:
    """Fetch the page with requests and parse HTML with BeautifulSoup."""
    listings = []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://www.volkswagen.co.uk/",
    }

    try:
        resp = requests.get(SEARCH_URL, headers=headers, timeout=30)
        print(f"  HTTP {resp.status_code}")
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                obj = json.loads(script.string)
                items = obj if isinstance(obj, list) else [obj]
                for item in items:
                    if item.get("@type") in ("Car", "Vehicle", "Product"):
                        listings.append(_normalize_ld_json(item))
            except (json.JSONDecodeError, TypeError):
                continue

        # Try embedded JSON state
        for script in soup.find_all("script"):
            text = script.string or ""
            for pattern in [
                r'window\.__INITIAL_STATE__\s*=\s*({.+?});',
                r'window\.__DATA__\s*=\s*({.+?});',
                r'window\.__NUXT__\s*=\s*({.+?});',
                r'"vehicles"\s*:\s*(\[.+?\])',
            ]:
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        vehicles = _find_vehicles_in_obj(data)
                        listings.extend(vehicles)
                    except json.JSONDecodeError:
                        continue

        if listings:
            print(f"  Extracted {len(listings)} listings from HTML.")
    except requests.RequestException as e:
        print(f"  Request failed: {e}")

    return listings


def _normalize_ld_json(item: dict) -> dict:
    """Convert a JSON-LD vehicle object to our standard format."""
    return {
        "title": item.get("name", ""),
        "price": str(
            item.get("offers", {}).get("price", "")
            if isinstance(item.get("offers"), dict)
            else ""
        ),
        "mileage": str(
            item.get("mileageFromOdometer", {}).get("value", "")
            if isinstance(item.get("mileageFromOdometer"), dict)
            else item.get("mileageFromOdometer", "")
        ),
        "year": item.get("vehicleModelDate", ""),
        "url": item.get("url", ""),
    }


def _parse_api_response(data: dict | list) -> list[dict]:
    """Parse a JSON API response into normalized listings."""
    listings = []

    vehicles = []
    if isinstance(data, list):
        vehicles = data
    elif isinstance(data, dict):
        vehicle_keys = {"results", "vehicles", "items", "data", "hits", "records",
                        "offers", "listings", "searchresults", "content", "docs",
                        "response", "inventory"}
        # Check top-level wrapper keys (case-insensitive)
        for key in list(data.keys()):
            kl = key.lower()
            if kl in vehicle_keys:
                if isinstance(data[key], list):
                    vehicles = data[key]
                    break
                # Check one level deeper (e.g. solr: data.search.results)
                if isinstance(data[key], dict):
                    for sub_key in data[key]:
                        if sub_key.lower() in vehicle_keys:
                            if isinstance(data[key][sub_key], list):
                                vehicles = data[key][sub_key]
                                break
                    if vehicles:
                        break

    for v in vehicles:
        if not isinstance(v, dict):
            continue
        listing = _extract_vehicle_fields(v)
        if listing["title"]:
            listings.append(listing)

    return listings


def _find_vehicles_in_obj(obj, depth=0) -> list[dict]:
    """Recursively search a nested object for vehicle-like entries."""
    results = []
    if depth > 10:
        return results

    if isinstance(obj, dict):
        keys_lower = {k.lower() for k in obj.keys()}
        keys_joined = " ".join(keys_lower)

        # Use substring matching so VW-specific keys like
        # 'price_retail_cur_flt' match the 'price' check
        has_price = any(
            kw in keys_joined
            for kw in ("price", "retailprice")
        )
        has_identity = any(
            kw in keys_joined
            for kw in ("title", "name", "model", "make", "manufacturer")
        )
        has_vehicle_detail = any(
            kw in keys_joined
            for kw in ("mileage", "odometer", "year", "registration",
                        "vin", "fuel", "transmission", "engine",
                        "colour", "color")
        )
        # Must have price + identity + at least one vehicle-specific field,
        # OR have 4+ vehicle-related keywords present
        vehicle_keywords = ["price", "mileage", "model", "make",
                            "title", "name", "year", "vin",
                            "registration", "fuel"]
        keyword_hits = sum(1 for kw in vehicle_keywords if kw in keys_joined)
        is_vehicle = (has_price and has_identity and has_vehicle_detail) or \
                     keyword_hits >= 4
        if is_vehicle:
            candidate = _extract_vehicle_fields(obj)
            # Only include if it looks like a real vehicle listing
            if _looks_like_vehicle(candidate):
                results.append(candidate)
        else:
            for v in obj.values():
                results.extend(_find_vehicles_in_obj(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_vehicles_in_obj(item, depth + 1))

    return results


def _extract_vehicle_fields(obj: dict) -> dict:
    """Extract vehicle fields from a dict, trying common and VW-specific key names."""
    # Build a case-insensitive lookup for the object
    lower_map = {k.lower(): k for k in obj.keys()}

    def _get(*candidates):
        """Return the first non-empty value matching any candidate (case-insensitive substring)."""
        # Try exact (case-insensitive) first
        for c in candidates:
            if c in lower_map:
                val = obj[lower_map[c]]
                if val not in (None, "", 0):
                    return val
        # Try substring match
        for c in candidates:
            for lk, orig_k in lower_map.items():
                if c in lk:
                    val = obj[orig_k]
                    if val not in (None, "", 0):
                        return val
        return ""

    title = (
        _get("title", "name")
        or f"{_get('make', 'manufacturer')} {_get('model')}".strip()
    )
    price = _get("price", "retailprice", "price_retail")
    mileage = _get("mileage", "odometer", "mileage_mil")
    year = _get("year", "registration", "initial_registration", "modelyear")
    url = _get("url", "link", "detailurl", "detail_url")

    # Format price with £ if it's a bare number
    price_str = str(price)
    if price_str and price_str.replace(",", "").replace(".", "").isdigit():
        try:
            price_str = f"£{int(float(price_str)):,}"
        except (ValueError, OverflowError):
            pass

    # Format mileage with "miles" suffix if bare number
    mileage_str = str(mileage)
    if mileage_str and mileage_str.replace(",", "").isdigit():
        try:
            mileage_str = f"{int(mileage_str):,} miles"
        except (ValueError, OverflowError):
            pass

    # Extract year (first 4 digits) from registration date strings
    year_str = str(year)
    if year_str and not re.match(r'^\d{4}$', year_str):
        year_match = re.search(r'(20[12]\d)', year_str)
        if year_match:
            year_str = year_match.group(1)

    return {
        "title": str(title),
        "price": price_str,
        "mileage": mileage_str,
        "year": year_str,
        "url": str(url),
    }


def _looks_like_vehicle(listing: dict) -> bool:
    """Check whether a candidate listing looks like a real vehicle."""
    title = str(listing.get("title") or listing.get("raw_text") or "")
    price = str(listing.get("price", ""))

    # Must have a non-empty title or raw_text
    if not title.strip():
        return False

    # Price must look numeric (digits, commas, dots, £ sign, or raw number)
    price_clean = price.replace("£", "").replace(",", "").replace(".", "").strip()
    if price_clean and not price_clean.isdigit():
        return False

    # Title should be more than just a single word (filters, labels)
    if len(title.split()) < 2:
        return False

    # Reject titles that are clearly UI elements, not car names
    title_lower = title.lower()
    ui_phrases = [
        "personalise", "personalize", "finance option", "vehicle details",
        "view details", "book a test", "cookie", "consent", "sign in",
        "subscribe", "newsletter", "calculate", "get a quote",
    ]
    if any(phrase in title_lower for phrase in ui_phrases):
        return False

    return True


def _filter_valid_listings(listings: list[dict]) -> list[dict]:
    """Filter out entries that are clearly not real vehicle listings."""
    valid = []
    for listing in listings:
        text = str(
            listing.get("title")
            or listing.get("raw_text")
            or ""
        ).lower()
        price = str(listing.get("price", ""))

        # Skip entries with no meaningful text
        if not text.strip():
            continue

        # Skip entries that are obviously not cars (common false positives)
        skip_phrases = [
            # Cookie / consent / legal
            "cookie", "consent", "privacy", "accept all", "terms and conditions",
            "data protection", "legal notice",
            # Account / auth
            "sign in", "sign up", "log in", "register", "my account",
            # Newsletter / marketing
            "newsletter", "subscribe", "feedback", "contact us",
            # UI controls
            "filter", "sort by", "show more", "load more",
            "compare", "save search", "create alert", "back to top",
            "next page", "previous page", "pagination",
            # Finance / insurance UI elements (not actual car listings)
            "personalise your finance", "personalize your finance",
            "finance options", "finance calculator", "apply for finance",
            "monthly payment", "representative example",
            "part exchange", "part-exchange",
            # Navigation / page sections
            "vehicle details", "view details", "more details",
            "book a test drive", "test drive", "request a callback",
            "calculate finance", "get a quote", "reserve this",
            "share this", "print this", "email this",
            "similar vehicles", "you may also like", "recently viewed",
            # Footer / header elements
            "find a retailer", "find a dealer", "locate dealer",
            "customer service", "help and support", "faq",
            "accessibility", "sitemap", "careers",
            # Social / sharing
            "follow us", "share on", "facebook", "twitter", "instagram",
        ]
        if any(phrase in text for phrase in skip_phrases):
            continue

        # If we have a price, it should be in a reasonable car range (£1,000 - £999,999)
        if price:
            price_digits = re.sub(r'[^\d]', '', price)
            if price_digits:
                price_num = int(price_digits)
                if price_num < 1000 or price_num > 999999:
                    continue

        # Positive validation: listing must look like an actual car
        # Require a VW model identifier in the text OR in the title specifically,
        # OR have strong structured vehicle signals (price + mileage or year)
        has_car_model = bool(re.search(
            r'(?:volkswagen|vw)\s+id[.\s]?[345]|id[.\s][345]',
            text, re.IGNORECASE,
        ))
        has_price = bool(price and re.sub(r'[^\d]', '', price))
        has_mileage = bool(listing.get("mileage"))
        has_year = bool(listing.get("year"))

        # Must have a recognisable car model name, OR have price + at least
        # one other vehicle attribute (mileage or year)
        if not has_car_model and not (has_price and (has_mileage or has_year)):
            continue

        valid.append(listing)
    return valid


def _deduplicate(listings: list[dict]) -> list[dict]:
    """Remove duplicate listings based on URL or title."""
    seen = set()
    unique = []
    for listing in listings:
        key = listing.get("url") or listing.get("title") or listing.get("raw_text", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(listing)
    return unique


def save_listings(listings: list[dict]) -> str:
    """Save listings to JSON file and return the path."""
    output = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "search_url": SEARCH_URL,
        "total_results": len(listings),
        "listings": listings,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(listings)} listings to {OUTPUT_FILE}")
    return OUTPUT_FILE


if __name__ == "__main__":
    listings = scrape_listings()
    save_listings(listings)
    if not listings:
        print("WARNING: No listings were found. Check debug_page.html and debug_screenshot.png for details.")
        sys.exit(1)
