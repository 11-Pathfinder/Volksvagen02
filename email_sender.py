"""
Email sender for VW Used Cars Scraper.
Formats scraped listings into an HTML email and sends via SMTP.
"""

import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

DEFAULT_RECIPIENT = "mohit.sanwal@gmail.com"
LISTINGS_FILE = "listings.json"


def load_listings(path: str = LISTINGS_FILE) -> dict:
    """Load scraped listings from JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_html_email(data: dict) -> str:
    """Build an HTML email body from listing data."""
    scraped_at = data.get("scraped_at", "Unknown")
    total = data.get("total_results", 0)
    listings = data.get("listings", [])
    search_url = data.get("search_url", "")

    # Format timestamp for display
    try:
        dt = datetime.fromisoformat(scraped_at)
        scraped_at_display = dt.strftime("%d %B %Y at %H:%M UTC")
    except Exception:
        scraped_at_display = scraped_at

    rows = ""
    for i, car in enumerate(listings, 1):
        title = car.get("title") or car.get("raw_text", "Unknown Vehicle")
        price = car.get("price", "N/A")
        mileage = car.get("mileage", "N/A")
        year = car.get("year", "N/A")
        url = car.get("url", "")

        # Clean up title if it's raw text
        if len(title) > 100:
            title = title[:100] + "..."

        link_cell = f'<a href="{url}" style="color:#0066cc;">View</a>' if url else "N/A"

        bg_color = "#f9f9f9" if i % 2 == 0 else "#ffffff"
        rows += f"""
        <tr style="background-color:{bg_color};">
            <td style="padding:10px;border-bottom:1px solid #e0e0e0;">{i}</td>
            <td style="padding:10px;border-bottom:1px solid #e0e0e0;">{title}</td>
            <td style="padding:10px;border-bottom:1px solid #e0e0e0;">{price}</td>
            <td style="padding:10px;border-bottom:1px solid #e0e0e0;">{mileage}</td>
            <td style="padding:10px;border-bottom:1px solid #e0e0e0;">{year}</td>
            <td style="padding:10px;border-bottom:1px solid #e0e0e0;">{link_cell}</td>
        </tr>"""

    if not rows:
        rows = """
        <tr>
            <td colspan="6" style="padding:20px;text-align:center;color:#666;">
                No listings found matching your criteria today.
            </td>
        </tr>"""

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:Arial,sans-serif;margin:0;padding:20px;background:#f5f5f5;">
        <div style="max-width:800px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 4px rgba(0,0,0,0.1);">
            <div style="background:#001e50;color:#fff;padding:20px 30px;">
                <h1 style="margin:0;font-size:22px;">Volkswagen Used Cars - Daily Report</h1>
                <p style="margin:8px 0 0;opacity:0.85;font-size:14px;">ID.4 &amp; ID.5 | Under &pound;30,000 | Under 20,000 miles | 2024+</p>
            </div>
            <div style="padding:20px 30px;">
                <p style="color:#333;font-size:14px;">
                    Scraped on <strong>{scraped_at_display}</strong> &mdash;
                    <strong>{total}</strong> vehicle(s) found.
                </p>
                <table style="width:100%;border-collapse:collapse;font-size:14px;">
                    <thead>
                        <tr style="background:#001e50;color:#fff;">
                            <th style="padding:10px;text-align:left;">#</th>
                            <th style="padding:10px;text-align:left;">Vehicle</th>
                            <th style="padding:10px;text-align:left;">Price</th>
                            <th style="padding:10px;text-align:left;">Mileage</th>
                            <th style="padding:10px;text-align:left;">Year</th>
                            <th style="padding:10px;text-align:left;">Link</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
                <p style="margin-top:20px;font-size:13px;color:#666;">
                    <a href="{search_url}" style="color:#0066cc;">View full search on Volkswagen UK</a>
                </p>
            </div>
            <div style="background:#f0f0f0;padding:15px 30px;font-size:12px;color:#888;text-align:center;">
                Automated report by VW Used Cars Scraper &bull; Unsubscribe by disabling the GitHub Actions workflow
            </div>
        </div>
    </body>
    </html>
    """
    return html


def build_plain_text(data: dict) -> str:
    """Build a plain-text fallback of the email."""
    total = data.get("total_results", 0)
    listings = data.get("listings", [])
    scraped_at = data.get("scraped_at", "Unknown")

    lines = [
        "Volkswagen Used Cars - Daily Report",
        "=" * 40,
        f"ID.4 & ID.5 | Under £30,000 | Under 20,000 miles | 2024+",
        f"Scraped: {scraped_at}",
        f"Total vehicles found: {total}",
        "",
    ]

    for i, car in enumerate(listings, 1):
        title = car.get("title") or car.get("raw_text", "Unknown Vehicle")
        price = car.get("price", "N/A")
        mileage = car.get("mileage", "N/A")
        year = car.get("year", "N/A")
        url = car.get("url", "")

        lines.append(f"{i}. {title}")
        lines.append(f"   Price: {price} | Mileage: {mileage} | Year: {year}")
        if url:
            lines.append(f"   Link: {url}")
        lines.append("")

    if not listings:
        lines.append("No listings found matching your criteria today.")

    lines.append(f"\nFull search: {data.get('search_url', '')}")
    return "\n".join(lines)


def send_email(
    recipient: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> None:
    """Send email via SMTP using environment variable credentials."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    sender = os.environ.get("EMAIL_FROM", smtp_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    print(f"Connecting to {smtp_host}:{smtp_port}...")
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(sender, [recipient], msg.as_string())

    print(f"Email sent successfully to {recipient}")


def main():
    recipient = os.environ.get("EMAIL_RECIPIENT", DEFAULT_RECIPIENT)
    listings_path = sys.argv[1] if len(sys.argv) > 1 else LISTINGS_FILE

    if not os.path.exists(listings_path):
        print(f"ERROR: Listings file not found: {listings_path}")
        sys.exit(1)

    data = load_listings(listings_path)
    total = data.get("total_results", 0)

    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    subject = f"VW ID.4/ID.5 Used Cars Report - {today} ({total} found)"

    html_body = build_html_email(data)
    text_body = build_plain_text(data)

    send_email(recipient, subject, html_body, text_body)


if __name__ == "__main__":
    main()
