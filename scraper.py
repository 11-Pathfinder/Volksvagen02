"""
VW Used Cars Scraper
Scrapes Volkswagen UK certified used car listings (ID.4 & ID.5)
and sends results via email.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

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


def scrape_listings() -> list[dict]:
    """Scrape VW used car listings using a headless browser."""
    listings = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        print(f"Navigating to: {SEARCH_URL}")
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
                print("Accepted cookie consent.")
                page.wait_for_timeout(2000)
        except Exception:
            print("No cookie banner found or already dismissed.")

        # Wait for vehicle cards to load
        page.wait_for_timeout(5000)

        # Scroll down to trigger lazy-loaded content
        for _ in range(5):
            page.keyboard.press("End")
            page.wait_for_timeout(1500)

        # Try multiple selectors that VW listing sites commonly use
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
                print(f"Found {count} listings using selector: {selector}")
                break

        if cards is None or cards.count() == 0:
            # Fallback: extract data from page content directly
            print("No card elements found with known selectors.")
            print("Attempting text-based extraction from page content...")

            content = page.content()
            # Save page source for debugging
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(content)
            print("Saved page HTML to debug_page.html for inspection.")

            # Try to extract listing data from embedded JSON/scripts
            listings = _extract_from_page_scripts(page)
            if not listings:
                listings = _extract_from_page_text(page)
        else:
            # Extract data from each card
            for i in range(cards.count()):
                card = cards.nth(i)
                listing = _extract_card_data(card, used_selector)
                if listing and listing.get("title"):
                    listings.append(listing)

        print(f"Extracted {len(listings)} listings total.")
        browser.close()

    return listings


def _extract_card_data(card, selector: str) -> dict:
    """Extract data from a single vehicle card element."""
    data = {}

    # Title / model name
    for title_sel in ["h2", "h3", ".title", "[class*='title']", "[class*='Title']"]:
        try:
            el = card.locator(title_sel).first
            if el.is_visible(timeout=500):
                data["title"] = el.inner_text().strip()
                break
        except Exception:
            continue

    # Price
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

    # Mileage
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

    # Year / registration
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

    # Link to details page
    try:
        link = card.locator("a[href]").first
        href = link.get_attribute("href")
        if href:
            if href.startswith("/"):
                href = "https://usedcars.volkswagen.co.uk" + href
            data["url"] = href
    except Exception:
        pass

    # If structured fields weren't found, grab full card text
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

    # Also try extracting from __NEXT_DATA__ or similar JS state
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
        "price": item.get("offers", {}).get("price", ""),
        "mileage": item.get("mileageFromOdometer", {}).get("value", "")
        if isinstance(item.get("mileageFromOdometer"), dict)
        else item.get("mileageFromOdometer", ""),
        "year": item.get("vehicleModelDate", ""),
        "url": item.get("url", ""),
    }


def _find_vehicles_in_obj(obj, depth=0) -> list[dict]:
    """Recursively search a nested object for vehicle-like entries."""
    results = []
    if depth > 10:
        return results

    if isinstance(obj, dict):
        # Check if this dict looks like a vehicle
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
        print(f"Found {count} detail links on the page.")
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
