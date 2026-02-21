"""
Main entry point: runs the scraper and sends results via email.
"""

import sys

from scraper import scrape_listings, save_listings
from email_sender import load_listings, build_html_email, build_plain_text, send_email

import os
from datetime import datetime, timezone


def main():
    print("=" * 50)
    print("VW Used Cars Scraper - Starting run")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

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

    try:
        send_email(recipient, subject, html_body, text_body)
        print("Email sent successfully!")
    except KeyError as e:
        print(f"ERROR: Missing environment variable {e}")
        print("Set SMTP_USER and SMTP_PASS to enable email sending.")
        print("Listings have been saved to listings.json")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR sending email: {e}")
        sys.exit(1)

    print("\nDone!")


if __name__ == "__main__":
    main()
