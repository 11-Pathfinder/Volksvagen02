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

from playwright.sync_api import sync_playwright


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
    "Chrome/125.0.0.0 Safari/537.36"
)


def scrape_listings() -> list[dict]:
    """Scrape VW used car listings using headless browser with network interception."""
    # Primary strategy: headless browser with network interception
    print("[Strategy 1] Headless browser with network interception...")
    listings = _scrape_via_browser()
    if not listings:
        # Fallback: requests + BeautifulSoup (works if site returns server-rendered HTML)
        print("[Strategy 2] Attempting requests + HTML parsing...")
        listings = _scrape_via_requests()

    # Post-extraction: filter out non-vehicle entries
    before = len(listings)
    listings = _filter_valid_listings(listings)
    after = len(listings)
    if before != after:
        print(f"  Filtered out {before - after} non-vehicle entries ({before} -> {after})")

    return listings


def _scrape_via_browser() -> list[dict]:
    """Scrape using Playwright headless Chromium with network interception."""
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
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-GB",
            )
            page = context.new_page()

            # Intercept network responses to capture API data
            page.on("response", handle_response)

            print(f"  Navigating to: {SEARCH_URL[:100]}...")
            page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)

            # Accept cookies if a consent banner appears
            _dismiss_cookie_banner(page)

            # Brief pause for dynamic content after networkidle
            page.wait_for_timeout(500)

            # Scroll to trigger lazy loading
            page.keyboard.press("End")
            page.wait_for_timeout(500)
            page.keyboard.press("End")
            page.wait_for_timeout(500)

            # === Extraction Phase ===

            # Step 1: Check captured API responses for vehicle data
            print(f"  Captured {len(captured_api_responses)} JSON API responses.")
            for resp_data in captured_api_responses:
                api_url = resp_data["url"]
                data = resp_data["data"]
                vehicles = _find_vehicles_in_obj(data)
                if vehicles:
                    print(f"  Found {len(vehicles)} vehicles from API: {api_url[:120]}")
                    listings.extend(vehicles)

            if listings:
                print(f"  Total from API interception: {len(listings)}")
                browser.close()
                return _deduplicate(listings)

            # Step 2: Try extracting from embedded scripts / JSON-LD
            print("  No vehicles from API calls. Trying embedded data...")
            listings = _extract_from_page_scripts(page)
            if listings:
                print(f"  Found {len(listings)} from embedded scripts.")
                browser.close()
                return listings

            # Step 3: Try DOM-based extraction with many selectors
            print("  Trying DOM-based extraction...")
            listings = _extract_from_dom(page)
            if listings:
                print(f"  Found {len(listings)} from DOM extraction.")
                browser.close()
                return listings

            # Step 4: Last resort - extract from visible text
            print("  Trying text-based extraction...")
            listings = _extract_from_page_text(page)

            # Save debug artifacts for CI inspection
            _save_debug_artifacts(page)
            _log_page_diagnostics(page, captured_api_responses)

            print(f"  Extracted {len(listings)} listings total.")
            browser.close()
    except Exception as e:
        print(f"  Browser scraping failed: {e}")

    return listings


def _dismiss_cookie_banner(page) -> None:
    """Try to dismiss cookie consent banners."""
    # Most likely selectors first; short timeouts to avoid wasting time
    cookie_selectors = [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
        "button:has-text('Accept All Cookies')",
        "[data-testid='cookie-accept']",
    ]
    for selector in cookie_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=500):
                btn.click()
                print(f"  Accepted cookies via: {selector}")
                page.wait_for_timeout(500)
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
                for i in range(min(count, 50)):
                    card = found.nth(i)
                    listing = _extract_card_data(card)
                    if listing and (listing.get("title") or listing.get("raw_text")):
                        listings.append(listing)
                if listings:
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

    # Link
    try:
        link = card.locator("a[href]").first
        href = link.get_attribute("href")
        if href:
            if href.startswith("/"):
                href = "https://usedcars.volkswagen.co.uk" + href
            data["url"] = href
    except Exception:
        pass

    # Fallback: grab full text from the card
    if not data.get("title"):
        try:
            full_text = card.inner_text().strip()
            if full_text:
                data["raw_text"] = full_text[:500]
                # Try to parse price from raw text
                price_match = re.search(r'\u00a3[\d,]+', full_text)
                if price_match:
                    data["price"] = price_match.group()
                # Try to parse year
                year_match = re.search(r'\b(202[0-9])\b', full_text)
                if year_match:
                    data["year"] = year_match.group(1)
                # Try to parse mileage
                mile_match = re.search(r'([\d,]+)\s*(?:miles|mi)', full_text, re.IGNORECASE)
                if mile_match:
                    data["mileage"] = mile_match.group(1) + " miles"
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
                            mile_match = re.search(
                                r'([\d,]+)\s*(?:miles|mi)',
                                text, re.IGNORECASE,
                            )
                            if mile_match:
                                listing["mileage"] = mile_match.group(1) + " miles"
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

    # Ultimate fallback: parse the entire body text for vehicle-like blocks
    try:
        body_text = page.inner_text("body")
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
            mile_match = re.search(r'([\d,]+)\s*(?:miles|mi)', block, re.IGNORECASE)
            if mile_match:
                listing["mileage"] = mile_match.group(1) + " miles"
            year_match = re.search(r'\b(202[0-9])\b', block)
            if year_match:
                listing["year"] = year_match.group(1)
            if listing.get("title") or listing.get("price"):
                listing.setdefault("raw_text", block[:500])
                listings.append(listing)

    return listings


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
        for key in ("results", "vehicles", "items", "data", "hits", "records",
                     "offers", "listings", "searchResults", "content"):
            if key in data and isinstance(data[key], list):
                vehicles = data[key]
                break

    for v in vehicles:
        if not isinstance(v, dict):
            continue
        listing = {
            "title": (
                v.get("title")
                or v.get("name")
                or f"{v.get('make', '')} {v.get('model', '')}".strip()
                or v.get("TITLE", "")
            ),
            "price": str(
                v.get("price")
                or v.get("retailPrice")
                or v.get("PRICE_RETAIL_CUR_FLT", "")
            ),
            "mileage": str(
                v.get("mileage")
                or v.get("odometerReading")
                or v.get("MILEAGE_MIL_INT", "")
            ),
            "year": str(
                v.get("year")
                or v.get("registrationDate", "")[:4]
                if v.get("registrationDate")
                else v.get("INITIAL_REGISTRATION_DTE", "")
            ),
            "url": v.get("url") or v.get("detailUrl") or "",
        }
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
        # Require strong evidence: price + at least one identifier (title/name/model)
        has_price = "price" in keys_lower or "retailprice" in keys_lower
        has_identity = bool(keys_lower & {"title", "name", "model", "make"})
        has_vehicle_detail = bool(keys_lower & {"mileage", "mileagefromodometer",
                                                 "year", "registration", "vin",
                                                 "fueltype", "fuel", "transmission",
                                                 "engine", "colour", "color"})
        # Must have price + identity + at least one vehicle-specific field,
        # OR have 4+ vehicle-related keys (strong signal)
        is_vehicle = (has_price and has_identity and has_vehicle_detail) or \
                     len(keys_lower & {"price", "mileage", "model", "make",
                                       "title", "name", "year", "vin",
                                       "registration", "fueltype"}) >= 4
        if is_vehicle:
            title = obj.get("title") or obj.get("name") or obj.get("model", "")
            price = str(obj.get("price") or obj.get("retailPrice", ""))
            candidate = {
                "title": title,
                "price": price,
                "mileage": str(obj.get("mileage", "")),
                "year": str(obj.get("year") or obj.get("registration", "")),
                "url": obj.get("url") or obj.get("link", ""),
            }
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
