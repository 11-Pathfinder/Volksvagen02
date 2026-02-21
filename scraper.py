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

# Known API endpoint used by the VW UK used cars site
API_BASE = "https://usedcars.volkswagen.co.uk/api"

OUTPUT_FILE = "listings.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Chromium path for environments with pre-installed browsers
CHROMIUM_PATH = os.environ.get(
    "CHROMIUM_PATH",
    "/root/.cache/ms-playwright/chromium-1194/chrome-linux/chrome",
)


def scrape_listings() -> list[dict]:
    """Scrape VW used car listings. Tries API first, then headless browser."""
    # Strategy 1: Try the underlying API (fastest, most reliable)
    print("[Strategy 1] Attempting API-based extraction...")
    listings = _scrape_via_api()
    if listings:
        return listings

    # Strategy 2: Try requests + BeautifulSoup (no browser needed)
    print("[Strategy 2] Attempting requests + HTML parsing...")
    listings = _scrape_via_requests()
    if listings:
        return listings

    # Strategy 3: Full headless browser
    print("[Strategy 3] Attempting headless browser extraction...")
    listings = _scrape_via_browser()
    return listings


def _scrape_via_api() -> list[dict]:
    """Try to hit the underlying search API directly."""
    listings = []

    # The VW used cars site typically uses a search API
    api_params = {
        "POOLS_CSV": "47-12532-217084",
        "RADIUS_LEN_FLT": "900",
        "PRICE_RETAIL_CUR_FLT_TO": "30000",
        "MILEAGE_MIL_INT_TO": "20000",
        "INITIAL_REGISTRATION_DTE_FROM": "2024",
        "search": "passenger",
        "MANUFACTURER_LST": "VOLKSWAGEN",
        "priceSwitch": "on",
        "MODEL_TYPE_LST": "VOLKSWAGEN_ID_4||VOLKSWAGEN_ID_5",
        "ZIP_LOC": "SW20 9DQ",
        "sort": "PRICE_RETAIL_CUR_FLT:ASC",
    }

    # Try common API endpoint patterns
    api_urls = [
        f"https://usedcars.volkswagen.co.uk/api/vehicle_search?{urlencode(api_params)}",
        f"https://usedcars.volkswagen.co.uk/api/v1/vehicles?{urlencode(api_params)}",
        f"https://usedcars.volkswagen.co.uk/svc/searches?{urlencode(api_params)}",
    ]

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://usedcars.volkswagen.co.uk/",
    }

    for url in api_urls:
        try:
            print(f"  Trying: {url[:80]}...")
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                listings = _parse_api_response(data)
                if listings:
                    print(f"  API returned {len(listings)} listings.")
                    return listings
        except (requests.RequestException, ValueError) as e:
            print(f"  Failed: {e}")
            continue

    print("  No API endpoint returned results.")
    return []


def _parse_api_response(data: dict | list) -> list[dict]:
    """Parse a JSON API response into normalized listings."""
    listings = []

    # Handle various response structures
    vehicles = []
    if isinstance(data, list):
        vehicles = data
    elif isinstance(data, dict):
        # Common keys for vehicle arrays
        for key in ("results", "vehicles", "items", "data", "hits", "records"):
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


def _scrape_via_browser() -> list[dict]:
    """Scrape using Playwright headless Chromium."""
    listings = []

    # Determine chromium executable
    executable = None
    if os.path.isfile(CHROMIUM_PATH):
        executable = CHROMIUM_PATH
        print(f"  Using Chromium at: {executable}")

    try:
        with sync_playwright() as p:
            launch_opts = {"headless": True}
            if executable:
                launch_opts["executable_path"] = executable

            browser = p.chromium.launch(**launch_opts)
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            print(f"  Navigating to: {SEARCH_URL}")
            page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)

            # Accept cookies if a consent banner appears
            try:
                cookie_btn = page.locator(
                    "button:has-text('Accept All'), "
                    "button:has-text('Accept all'), "
                    "button:has-text('agree'), "
                    "button:has-text('Allow all'), "
                    "#onetrust-accept-btn-handler"
                ).first
                if cookie_btn.is_visible(timeout=5000):
                    cookie_btn.click()
                    print("  Accepted cookie consent.")
                    page.wait_for_timeout(2000)
            except Exception:
                pass

            # Wait for content and scroll to trigger lazy loading
            page.wait_for_timeout(5000)
            for _ in range(5):
                page.keyboard.press("End")
                page.wait_for_timeout(1500)

            # Try multiple selectors
            card_selectors = [
                "[data-testid='vehicle-card']",
                ".vehicle-card",
                ".result-list-entry",
                ".car-card",
                ".listing-item",
                "article.vehicle",
                "[class*='VehicleCard']",
                "[class*='vehicle-card']",
                "[class*='ResultList'] > div",
                ".sc-vehicle-tile",
                "a[href*='/vehicle_detail/']",
            ]

            cards = None
            used_selector = None
            for selector in card_selectors:
                found = page.locator(selector)
                count = found.count()
                if count > 0:
                    cards = found
                    used_selector = selector
                    print(f"  Found {count} listings using selector: {selector}")
                    break

            if cards is None or cards.count() == 0:
                print("  No card elements found with known selectors.")
                content = page.content()
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(content)
                print("  Saved page HTML to debug_page.html for inspection.")

                listings = _extract_from_page_scripts(page)
                if not listings:
                    listings = _extract_from_page_text(page)
            else:
                for i in range(cards.count()):
                    card = cards.nth(i)
                    listing = _extract_card_data(card, used_selector)
                    if listing and (listing.get("title") or listing.get("raw_text")):
                        listings.append(listing)

            print(f"  Extracted {len(listings)} listings total.")
            browser.close()
    except Exception as e:
        print(f"  Browser scraping failed: {e}")

    return listings


def _extract_card_data(card, selector: str) -> dict:
    """Extract data from a single vehicle card element."""
    data = {}

    for title_sel in ["h2", "h3", ".title", "[class*='title']", "[class*='Title']"]:
        try:
            el = card.locator(title_sel).first
            if el.is_visible(timeout=500):
                data["title"] = el.inner_text().strip()
                break
        except Exception:
            continue

    for price_sel in [
        "[class*='price']", "[class*='Price']",
        "[data-testid*='price']", ".price",
    ]:
        try:
            el = card.locator(price_sel).first
            if el.is_visible(timeout=500):
                text = el.inner_text().strip()
                if "\u00a3" in text or "GBP" in text or text.replace(",", "").replace(".", "").isdigit():
                    data["price"] = text
                    break
        except Exception:
            continue

    for mile_sel in [
        "[class*='mileage']", "[class*='Mileage']",
        "[class*='kilometer']", "[class*='Kilometer']",
    ]:
        try:
            el = card.locator(mile_sel).first
            if el.is_visible(timeout=500):
                data["mileage"] = el.inner_text().strip()
                break
        except Exception:
            continue

    for year_sel in [
        "[class*='year']", "[class*='Year']",
        "[class*='registration']", "[class*='Registration']",
    ]:
        try:
            el = card.locator(year_sel).first
            if el.is_visible(timeout=500):
                data["year"] = el.inner_text().strip()
                break
        except Exception:
            continue

    try:
        link = card.locator("a[href]").first
        href = link.get_attribute("href")
        if href:
            if href.startswith("/"):
                href = "https://usedcars.volkswagen.co.uk" + href
            data["url"] = href
    except Exception:
        pass

    if not data.get("title"):
        try:
            full_text = card.inner_text().strip()
            if full_text:
                data["raw_text"] = full_text[:500]
        except Exception:
            pass

    return data


def _extract_from_page_scripts(page) -> list[dict]:
    """Try to extract listing data from embedded script tags / JSON."""
    listings = []
    try:
        scripts = page.locator("script[type='application/ld+json']")
        for i in range(scripts.count()):
            try:
                text = scripts.nth(i).inner_text()
                obj = json.loads(text)
                if isinstance(obj, list):
                    for item in obj:
                        if item.get("@type") in ("Car", "Vehicle", "Product"):
                            listings.append(_normalize_ld_json(item))
                elif isinstance(obj, dict):
                    if obj.get("@type") in ("Car", "Vehicle", "Product"):
                        listings.append(_normalize_ld_json(obj))
            except Exception:
                continue
    except Exception:
        pass

    try:
        data = page.evaluate("""() => {
            if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__);
            if (window.__NUXT__) return JSON.stringify(window.__NUXT__);
            if (window.__APP_STATE__) return JSON.stringify(window.__APP_STATE__);
            return null;
        }""")
        if data:
            parsed = json.loads(data)
            vehicles = _find_vehicles_in_obj(parsed)
            listings.extend(vehicles)
    except Exception:
        pass

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


def _find_vehicles_in_obj(obj, depth=0) -> list[dict]:
    """Recursively search a nested object for vehicle-like entries."""
    results = []
    if depth > 10:
        return results

    if isinstance(obj, dict):
        keys_lower = {k.lower() for k in obj.keys()}
        vehicle_keys = {"price", "mileage", "model", "make", "title", "name"}
        if len(keys_lower & vehicle_keys) >= 2:
            results.append({
                "title": obj.get("title") or obj.get("name") or obj.get("model", ""),
                "price": str(obj.get("price", "")),
                "mileage": str(obj.get("mileage", "")),
                "year": str(obj.get("year") or obj.get("registration", "")),
                "url": obj.get("url") or obj.get("link", ""),
            })
        else:
            for v in obj.values():
                results.extend(_find_vehicles_in_obj(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_vehicles_in_obj(item, depth + 1))

    return results


def _extract_from_page_text(page) -> list[dict]:
    """Last-resort: extract listings from visible page text."""
    listings = []
    try:
        all_links = page.locator("a[href*='vehicle_detail']")
        count = all_links.count()
        print(f"  Found {count} detail links on the page.")
        for i in range(count):
            link = all_links.nth(i)
            try:
                href = link.get_attribute("href") or ""
                text = link.inner_text().strip()
                if href.startswith("/"):
                    href = "https://usedcars.volkswagen.co.uk" + href
                if text:
                    listings.append({
                        "raw_text": text[:500],
                        "url": href,
                    })
            except Exception:
                continue
    except Exception:
        pass

    return listings


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
        print("WARNING: No listings were found. Check debug_page.html for details.")
        sys.exit(1)
