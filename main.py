"""
Main entry point: runs the scraper and sends results via email.
"""

import os
import sys
import traceback
from datetime import datetime, timezone


def main():
    print("=" * 50)
    print("VW Used Cars Scraper - Starting run")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"Python: {sys.version}")
    print("=" * 50)

    # Lazy imports so we get clear error messages if a dependency is missing
    try:
        from scraper import scrape_listings, save_listings
    except ImportError as e:
        print(f"ERROR importing scraper: {e}")
        traceback.print_exc()
        return

    try:
        from email_sender import load_listings, build_html_email, build_plain_text, send_email
    except ImportError as e:
        print(f"ERROR importing email_sender: {e}")
        traceback.print_exc()
        return

    # Step 1: Scrape listings
    print("\n[1/2] Scraping VW used car listings...")
    listings = scrape_listings()
    listings_path = save_listings(listings)

    total = len(listings)
    print(f"Found {total} listings.")

    # Step 2: Send email
    print("\n[2/2] Sending email report...")
    recipient = os.environ.get("EMAIL_RECIPIENT", "mohit.sanwal@gmail.com")

    data = load_listings(listings_path)
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    subject = f"VW ID.4/ID.5 Used Cars Report - {today} ({total} found)"

    html_body = build_html_email(data)
    text_body = build_plain_text(data)

    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not smtp_user or not smtp_pass:
        print("SMTP_USER / SMTP_PASS not set — skipping email.")
        print(f"Listings saved to {listings_path}")
    else:
        try:
            send_email(recipient, subject, html_body, text_body)
            print("Email sent successfully!")
        except Exception as e:
            print(f"WARNING: email sending failed: {e}")
            print(f"Listings saved to {listings_path}")

    print("\nDone!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        # Don't exit(1) — let the artifacts upload step run
