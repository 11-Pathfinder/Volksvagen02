# VW Used Cars Scraper

Automated daily scraper for Volkswagen UK certified used cars (ID.4 & ID.5 under £30,000) that emails results to a specified recipient.

## How It Works

1. **Scraper** (`scraper.py`) — Uses Playwright to load the VW used cars search page in a headless Chromium browser, extracts vehicle listings with price, mileage, year, and detail links.
2. **Email** (`email_sender.py`) — Builds a formatted HTML email with all found listings and sends it via SMTP.
3. **Scheduler** (`.github/workflows/daily_scrape.yml`) — GitHub Actions cron job triggers at 7:00 AM UTC daily.

## Quick Start

### Local Testing

```bash
pip install -r requirements.txt
playwright install chromium

# Run scraper only (saves to listings.json)
python scraper.py

# Run full pipeline (scrape + email)
export SMTP_USER="your-email@gmail.com"
export SMTP_PASS="your-app-password"
python main.py
```

### GitHub Actions Setup

1. Push this repo to GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Add these repository secrets:
   - `SMTP_USER` — Your Gmail address
   - `SMTP_PASS` — A [Gmail App Password](https://support.google.com/accounts/answer/185833)
4. The workflow runs automatically at 7:00 AM UTC daily. You can also trigger it manually from the **Actions** tab.

## Files

| File | Purpose |
|---|---|
| `scraper.py` | Headless browser scraper using Playwright |
| `email_sender.py` | HTML email builder and SMTP sender |
| `main.py` | Entry point — runs scrape then emails results |
| `requirements.txt` | Python dependencies |
| `.github/workflows/daily_scrape.yml` | GitHub Actions daily schedule |
| `PROMPT.md` | Project specification |
