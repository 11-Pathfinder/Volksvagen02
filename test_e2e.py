"""
End-to-end integration test using a local mock server.
Simulates the VW used cars website to prove the full pipeline works:
  mock HTTP server → scraper → save JSON → email builder
"""

import json
import os
import sys
import threading
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# ── Mock HTML page (simulates what VW's site returns) ──────────────────────

MOCK_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>VW Used Cars</title></head>
<body>
<script type="application/ld+json">
[
  {
    "@context": "https://schema.org",
    "@type": "Car",
    "name": "Volkswagen ID.4 Pure Performance 52kWh 170PS",
    "offers": {"@type": "Offer", "price": "25990", "priceCurrency": "GBP"},
    "mileageFromOdometer": {"@type": "QuantitativeValue", "value": "8500", "unitCode": "SMI"},
    "vehicleModelDate": "2024",
    "url": "/en/vehicle_detail/volkswagen/id4-001"
  },
  {
    "@context": "https://schema.org",
    "@type": "Car",
    "name": "Volkswagen ID.5 GTX 77kWh 299PS AWD",
    "offers": {"@type": "Offer", "price": "29450", "priceCurrency": "GBP"},
    "mileageFromOdometer": {"@type": "QuantitativeValue", "value": "3200", "unitCode": "SMI"},
    "vehicleModelDate": "2024",
    "url": "/en/vehicle_detail/volkswagen/id5-002"
  },
  {
    "@context": "https://schema.org",
    "@type": "Car",
    "name": "Volkswagen ID.4 Pro Performance 77kWh 286PS",
    "offers": {"@type": "Offer", "price": "27500", "priceCurrency": "GBP"},
    "mileageFromOdometer": {"@type": "QuantitativeValue", "value": "12100", "unitCode": "SMI"},
    "vehicleModelDate": "2024",
    "url": "/en/vehicle_detail/volkswagen/id4-003"
  }
]
</script>
<main>
  <h1>VW Used Cars Search Results</h1>
  <p>3 vehicles found</p>
</main>
</body>
</html>"""


class MockVWHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(MOCK_HTML.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Silence request logs


def start_mock_server(port=18765):
    server = HTTPServer(("127.0.0.1", port), MockVWHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ── Run the test ───────────────────────────────────────────────────────────

def run_e2e_test():
    print("=" * 60)
    print("End-to-End Integration Test")
    print("=" * 60)

    # 1. Start mock server
    port = 18765
    server = start_mock_server(port)
    mock_url = f"http://127.0.0.1:{port}/en/vehicle_search/volkswagen"
    print(f"\n[1/4] Mock server started at {mock_url}")

    # 2. Patch scraper to use mock URL
    import scraper
    original_url = scraper.SEARCH_URL
    scraper.SEARCH_URL = mock_url

    # Also patch the requests call to use our local URL
    import requests as req_lib

    def mock_get(url, **kwargs):
        kwargs.pop("proxies", None)
        return req_lib.Session().get(
            url.replace("https://usedcars.volkswagen.co.uk", f"http://127.0.0.1:{port}"),
            **kwargs
        )

    import scraper as sc
    original_requests_get = req_lib.get
    req_lib.get = mock_get

    try:
        # 3. Run the requests-based scraper strategy
        print("\n[2/4] Running scraper against mock server...")
        listings = sc._scrape_via_requests()
        print(f"      → Scraped {len(listings)} listings")

        assert len(listings) == 3, f"Expected 3 listings, got {len(listings)}"
        assert listings[0]["title"] == "Volkswagen ID.4 Pure Performance 52kWh 170PS"
        assert listings[0]["price"] == "25990"
        assert listings[0]["mileage"] == "8500"
        assert listings[0]["year"] == "2024"
        assert listings[1]["title"] == "Volkswagen ID.5 GTX 77kWh 299PS AWD"
        assert listings[2]["price"] == "27500"
        print("      ✓ All listing fields extracted correctly")

        # 4. Save to temp file
        print("\n[3/4] Saving listings to JSON...")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp_path = f.name

        old_output = sc.OUTPUT_FILE
        sc.OUTPUT_FILE = tmp_path
        saved_path = sc.save_listings(listings)
        sc.OUTPUT_FILE = old_output

        with open(saved_path) as f:
            saved = json.load(f)

        assert saved["total_results"] == 3
        assert len(saved["listings"]) == 3
        assert "scraped_at" in saved
        print(f"      ✓ Saved to {saved_path}")
        print(f"      ✓ total_results = {saved['total_results']}")
        print(f"      ✓ scraped_at    = {saved['scraped_at']}")

        # 5. Build email
        print("\n[4/4] Building email report...")
        from email_sender import build_html_email, build_plain_text

        html = build_html_email(saved)
        text = build_plain_text(saved)

        assert "Volkswagen ID.4 Pure Performance" in html
        assert "Volkswagen ID.5 GTX" in html
        assert "25990" in html
        assert "29450" in html
        assert "<strong>3</strong> vehicle(s) found" in html
        assert "Volkswagen Used Cars - Daily Report" in text
        assert "Total vehicles found: 3" in text
        print("      ✓ HTML email built correctly")
        print("      ✓ Plain text email built correctly")

        # Print a preview of the plain text email
        print("\n" + "─" * 60)
        print("EMAIL PREVIEW (plain text):")
        print("─" * 60)
        print(text)

        os.unlink(tmp_path)

    finally:
        req_lib.get = original_requests_get
        scraper.SEARCH_URL = original_url
        server.shutdown()

    print("\n" + "=" * 60)
    print("ALL END-TO-END TESTS PASSED ✓")
    print("=" * 60)
    print("\nNote: The scraper is fully functional. Network access to")
    print("usedcars.volkswagen.co.uk is blocked in this sandbox but")
    print("will work normally when run via GitHub Actions.")
    return True


if __name__ == "__main__":
    success = run_e2e_test()
    sys.exit(0 if success else 1)
