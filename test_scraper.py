"""
Tests for the VW Used Cars Scraper.
Validates scraper logic, email formatting, and data parsing with mock data.
"""

import json
import os
import sys
import tempfile

# ── Scraper unit tests ──────────────────────────────────────────────────────

from scraper import (
    _find_vehicles_in_obj,
    _normalize_ld_json,
    _parse_api_response,
    _parse_listings_from_text,
    _filter_valid_listings,
    _deduplicate,
    save_listings,
)
from email_sender import build_html_email, build_plain_text, load_listings


SAMPLE_LISTINGS = [
    {
        "title": "Volkswagen ID.4 Pure Performance 52kWh 170PS 1-speed automatic",
        "price": "£25,990",
        "mileage": "8,500 miles",
        "year": "2024",
        "url": "https://usedcars.volkswagen.co.uk/en/vehicle_search/volkswagen/id-4/pure-perf-abc123/offer",
    },
    {
        "title": "Volkswagen ID.5 GTX 77kWh 299PS AWD",
        "price": "£29,450",
        "mileage": "3,200 miles",
        "year": "2024",
        "url": "https://usedcars.volkswagen.co.uk/en/vehicle_search/volkswagen/id-5/gtx-def456/offer",
    },
    {
        "title": "Volkswagen ID.4 Pro Performance 77kWh 286PS",
        "price": "£27,500",
        "mileage": "12,100 miles",
        "year": "2024",
        "url": "https://usedcars.volkswagen.co.uk/en/vehicle_search/volkswagen/id-4/pro-perf-ghi789/offer",
    },
]


def test_find_vehicles_in_obj():
    """Test recursive vehicle extraction from nested JSON."""
    nested = {
        "data": {
            "results": [
                {"title": "VW ID.4", "price": 25000, "mileage": 5000, "year": 2024},
                {"title": "VW ID.5", "price": 29000, "mileage": 3000, "year": 2024},
            ]
        }
    }
    found = _find_vehicles_in_obj(nested)
    assert len(found) == 2, f"Expected 2 vehicles, got {len(found)}"
    assert found[0]["title"] == "VW ID.4"
    assert found[1]["title"] == "VW ID.5"
    print("  PASS test_find_vehicles_in_obj")


def test_find_vehicles_empty():
    """Test with no vehicles present."""
    data = {"config": {"theme": "dark"}, "user": {"name": "test"}}
    found = _find_vehicles_in_obj(data)
    assert len(found) == 0, f"Expected 0 vehicles, got {len(found)}"
    print("  PASS test_find_vehicles_empty")


def test_find_vehicles_depth_limit():
    """Test that deeply nested structures don't cause issues."""
    data = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": {"k": {
        "title": "Too Deep", "price": 100, "mileage": 50
    }}}}}}}}}}}}
    found = _find_vehicles_in_obj(data)
    # Depth limit is 10, this is 11 deep — should not find it
    assert len(found) == 0, f"Expected 0 (too deep), got {len(found)}"
    print("  PASS test_find_vehicles_depth_limit")


def test_normalize_ld_json():
    """Test JSON-LD normalization."""
    ld = {
        "@type": "Car",
        "name": "VW ID.4 Pure",
        "offers": {"price": "25990", "priceCurrency": "GBP"},
        "mileageFromOdometer": {"value": "8500", "unitCode": "SMI"},
        "vehicleModelDate": "2024",
        "url": "/vehicle/123",
    }
    result = _normalize_ld_json(ld)
    assert result["title"] == "VW ID.4 Pure"
    assert result["price"] == "25990"
    assert result["mileage"] == "8500"
    assert result["year"] == "2024"
    print("  PASS test_normalize_ld_json")


def test_normalize_ld_json_scalar_mileage():
    """Test JSON-LD normalization with scalar mileage."""
    ld = {
        "name": "VW ID.5",
        "offers": {"price": "29000"},
        "mileageFromOdometer": "3200",
        "vehicleModelDate": "2024",
    }
    result = _normalize_ld_json(ld)
    assert result["mileage"] == "3200"
    print("  PASS test_normalize_ld_json_scalar_mileage")


def test_parse_api_response_results_key():
    """Test API response parsing with 'results' key."""
    api_data = {
        "results": [
            {"title": "VW ID.4", "price": 25000, "mileage": 8000},
            {"title": "VW ID.5", "price": 29000, "mileage": 3000},
        ],
        "total": 2,
    }
    listings = _parse_api_response(api_data)
    assert len(listings) == 2
    assert listings[0]["title"] == "VW ID.4"
    assert listings[0]["price"] == "25000"
    print("  PASS test_parse_api_response_results_key")


def test_parse_api_response_list():
    """Test API response parsing when response is a list."""
    api_data = [
        {"title": "VW ID.4", "price": 25000},
    ]
    listings = _parse_api_response(api_data)
    assert len(listings) == 1
    print("  PASS test_parse_api_response_list")


def test_parse_api_response_make_model():
    """Test API response with make/model instead of title."""
    api_data = {
        "vehicles": [
            {"make": "Volkswagen", "model": "ID.4", "price": 25000},
        ]
    }
    listings = _parse_api_response(api_data)
    assert len(listings) == 1
    assert listings[0]["title"] == "Volkswagen ID.4"
    print("  PASS test_parse_api_response_make_model")


def test_parse_api_response_empty():
    """Test API response with no vehicles."""
    assert _parse_api_response({}) == []
    assert _parse_api_response({"results": []}) == []
    assert _parse_api_response([]) == []
    print("  PASS test_parse_api_response_empty")


# ── Text parsing tests ────────────────────────────────────────────────────

def test_parse_listings_from_text():
    """Test extracting listings from unstructured text."""
    text = """
Some header text

Volkswagen ID.4 Pure Performance 52kWh 170PS
£25,990
8,500 miles
2024

Volkswagen ID.5 GTX 77kWh 299PS AWD
£29,450
3,200 miles
2024
"""
    listings = _parse_listings_from_text(text)
    assert len(listings) == 2, f"Expected 2 listings, got {len(listings)}"
    assert "ID.4" in listings[0].get("title", listings[0].get("raw_text", ""))
    assert listings[0]["price"] == "£25,990"
    print("  PASS test_parse_listings_from_text")


def test_parse_listings_from_text_empty():
    """Test text parsing with no vehicle data."""
    text = "Welcome to our website. Please search for vehicles."
    listings = _parse_listings_from_text(text)
    assert len(listings) == 0
    print("  PASS test_parse_listings_from_text_empty")


def test_find_vehicles_rejects_weak_matches():
    """Test that objects with only 2 generic keys are not matched."""
    # Only 'name' and 'price' without any vehicle-specific field -> rejected
    data = {
        "filters": [
            {"name": "ID.4", "price": 25000},
        ]
    }
    found = _find_vehicles_in_obj(data)
    assert len(found) == 0, f"Expected 0 (weak match), got {len(found)}"
    print("  PASS test_find_vehicles_rejects_weak_matches")


def test_filter_valid_listings():
    """Test that non-car entries are filtered out."""
    listings = [
        {"title": "Volkswagen ID.4 Pure Performance", "price": "£25,990"},
        {"title": "Cookie consent preferences", "price": ""},
        {"title": "Sign in to your account", "price": ""},
        {"title": "X", "price": "£5"},  # single-word title, tiny price
        {"title": "Volkswagen ID.5 GTX 77kWh", "price": "£29,000"},
    ]
    valid = _filter_valid_listings(listings)
    titles = [l.get("title", "") for l in valid]
    assert len(valid) == 2, f"Expected 2 valid, got {len(valid)}: {titles}"
    assert "Volkswagen ID.4 Pure Performance" in titles
    assert "Volkswagen ID.5 GTX 77kWh" in titles
    print("  PASS test_filter_valid_listings")


def test_filter_rejects_ui_elements():
    """Test that common website UI elements are filtered out."""
    listings = [
        # Valid cars - should pass
        {"title": "Volkswagen ID.4 Pure Performance 52kWh", "price": "£25,990"},
        {"title": "VW ID.5 GTX 77kWh 299PS AWD", "price": "£29,450"},
        # Non-car UI elements - should be filtered
        {"title": "Personalise your finance", "price": ""},
        {"title": "Vehicle details", "price": ""},
        {"title": "Book a test drive", "price": ""},
        {"title": "View details for this car", "price": ""},
        {"title": "Calculate finance options", "price": ""},
        {"title": "Find a retailer near you", "price": ""},
        {"title": "Share this vehicle", "price": ""},
        {"title": "Representative example for finance", "price": ""},
        {"title": "Part exchange your car", "price": ""},
        {"title": "Monthly payment calculator", "price": ""},
    ]
    valid = _filter_valid_listings(listings)
    titles = [l.get("title", "") for l in valid]
    assert len(valid) == 2, f"Expected 2 valid, got {len(valid)}: {titles}"
    assert "Volkswagen ID.4 Pure Performance 52kWh" in titles
    assert "VW ID.5 GTX 77kWh 299PS AWD" in titles
    print("  PASS test_filter_rejects_ui_elements")


def test_filter_requires_positive_car_signal():
    """Test that listings without car model or price+details are rejected."""
    listings = [
        # Has model name - should pass
        {"title": "Volkswagen ID.4 Pure", "price": "£25,000"},
        # Has price + mileage but no model - should pass (could be a valid listing
        # with a generic title)
        {"title": "Used Electric SUV", "price": "£27,000", "mileage": "5,000 miles"},
        # Has price + year but no model - should pass
        {"title": "Electric Vehicle Offer", "price": "£28,000", "year": "2024"},
        # Only has title, no price/mileage/year/model - should be rejected
        {"title": "Some random text entry", "price": ""},
        # Only has price, no model/mileage/year - should be rejected
        {"title": "Random listing title here", "price": "£20,000"},
    ]
    valid = _filter_valid_listings(listings)
    titles = [l.get("title", "") for l in valid]
    assert len(valid) == 3, f"Expected 3 valid, got {len(valid)}: {titles}"
    assert "Volkswagen ID.4 Pure" in titles
    assert "Used Electric SUV" in titles
    assert "Electric Vehicle Offer" in titles
    print("  PASS test_filter_requires_positive_car_signal")


def test_deduplicate():
    """Test deduplication of listings."""
    listings = [
        {"title": "VW ID.4", "url": "https://example.com/1"},
        {"title": "VW ID.5", "url": "https://example.com/2"},
        {"title": "VW ID.4", "url": "https://example.com/1"},  # duplicate
    ]
    unique = _deduplicate(listings)
    assert len(unique) == 2, f"Expected 2 unique, got {len(unique)}"
    print("  PASS test_deduplicate")


# ── Save/Load tests ────────────────────────────────────────────────────────

def test_save_and_load_listings():
    """Test saving listings to JSON and loading them back."""
    original_output = os.environ.get("_ORIG_OUTPUT")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        tmp_path = f.name

    try:
        # Monkey-patch OUTPUT_FILE for the test
        import scraper
        old_output = scraper.OUTPUT_FILE
        scraper.OUTPUT_FILE = tmp_path

        save_listings(SAMPLE_LISTINGS)
        data = load_listings(tmp_path)

        assert data["total_results"] == 3
        assert len(data["listings"]) == 3
        assert data["listings"][0]["title"] == SAMPLE_LISTINGS[0]["title"]
        assert "scraped_at" in data
        assert "search_url" in data

        scraper.OUTPUT_FILE = old_output
        print("  PASS test_save_and_load_listings")
    finally:
        os.unlink(tmp_path)


# ── Email formatting tests ─────────────────────────────────────────────────

def test_build_html_email():
    """Test HTML email generation contains expected content."""
    data = {
        "scraped_at": "2025-01-15T07:00:00+00:00",
        "search_url": "https://usedcars.volkswagen.co.uk/...",
        "total_results": 3,
        "listings": SAMPLE_LISTINGS,
    }
    html = build_html_email(data)

    assert "Volkswagen Used Cars - Daily Report" in html
    assert "ID.4" in html
    assert "ID.5" in html
    assert "£25,990" in html
    assert "£29,450" in html
    assert "8,500 miles" in html
    assert "<strong>3</strong> vehicle(s) found" in html
    assert "vehicle_search" in html
    print("  PASS test_build_html_email")


def test_build_html_email_empty():
    """Test HTML email with no listings."""
    data = {
        "scraped_at": "2025-01-15T07:00:00+00:00",
        "search_url": "https://example.com",
        "total_results": 0,
        "listings": [],
    }
    html = build_html_email(data)
    assert "No listings found" in html
    assert "<strong>0</strong> vehicle(s) found" in html
    print("  PASS test_build_html_email_empty")


def test_build_plain_text():
    """Test plain text email generation."""
    data = {
        "scraped_at": "2025-01-15T07:00:00+00:00",
        "search_url": "https://example.com",
        "total_results": 3,
        "listings": SAMPLE_LISTINGS,
    }
    text = build_plain_text(data)

    assert "Volkswagen Used Cars - Daily Report" in text
    assert "VW ID.4" in text or "ID.4" in text
    assert "£25,990" in text
    assert "Total vehicles found: 3" in text
    print("  PASS test_build_plain_text")


def test_build_plain_text_empty():
    """Test plain text email with no listings."""
    data = {
        "scraped_at": "2025-01-15T07:00:00+00:00",
        "search_url": "https://example.com",
        "total_results": 0,
        "listings": [],
    }
    text = build_plain_text(data)
    assert "No listings found" in text
    print("  PASS test_build_plain_text_empty")


def test_html_email_truncates_long_titles():
    """Test that very long titles are truncated in the email."""
    data = {
        "scraped_at": "2025-01-15T07:00:00+00:00",
        "search_url": "https://example.com",
        "total_results": 1,
        "listings": [{"raw_text": "A" * 200, "url": ""}],
    }
    html = build_html_email(data)
    # Should truncate to 100 chars + "..."
    assert "A" * 100 + "..." in html
    print("  PASS test_html_email_truncates_long_titles")


# ── Runner ──────────────────────────────────────────────────────────────────

def run_all_tests():
    """Run all tests and report results."""
    tests = [
        test_find_vehicles_in_obj,
        test_find_vehicles_empty,
        test_find_vehicles_depth_limit,
        test_normalize_ld_json,
        test_normalize_ld_json_scalar_mileage,
        test_parse_api_response_results_key,
        test_parse_api_response_list,
        test_parse_api_response_make_model,
        test_parse_api_response_empty,
        test_parse_listings_from_text,
        test_parse_listings_from_text_empty,
        test_find_vehicles_rejects_weak_matches,
        test_filter_valid_listings,
        test_filter_rejects_ui_elements,
        test_filter_requires_positive_car_signal,
        test_deduplicate,
        test_save_and_load_listings,
        test_build_html_email,
        test_build_html_email_empty,
        test_build_plain_text,
        test_build_plain_text_empty,
        test_html_email_truncates_long_titles,
    ]

    passed = 0
    failed = 0
    errors = []

    print(f"\nRunning {len(tests)} tests...\n")

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print(f"  FAIL {test.__name__}: {e}")

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    print(f"{'=' * 50}")

    if errors:
        print("\nFailures:")
        for name, err in errors:
            print(f"  - {name}: {err}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
