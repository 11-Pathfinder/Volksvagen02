# VW Used Cars Scraper

## Objective

Build a tool that scrapes Volkswagen UK certified used car listings (ID.4 & ID.5) daily at 7:00 AM and emails the results.

## Search Criteria

- **Models:** Volkswagen ID.4, Volkswagen ID.5
- **Max Price:** £30,000
- **Max Mileage:** 20,000 miles
- **Year:** 2024 onwards
- **Location:** SW20 9DQ (900-mile radius)
- **Sort:** Price ascending

## Source URL

```
https://usedcars.volkswagen.co.uk/en/vehicle_search/volkswagen?POOLS_CSV=47-12532-217084&RADIUS_LEN_FLT=900&PRICE_RETAIL_CUR_FLT_TO=30000&MILEAGE_MIL_INT_TO=20000&INITIAL_REGISTRATION_DTE_FROM=2024&search=passenger&MANUFACTURER_LST=VOLKSWAGEN&priceSwitch=on&MODEL_TYPE_LST=VOLKSWAGEN_ID_4||VOLKSWAGEN_ID_5&ZIP_LOC=SW20%209DQ&sort=PRICE_RETAIL_CUR_FLT:ASC
```

## Requirements

1. **Scraper** - Uses Playwright headless browser to load the JavaScript-rendered page and extract vehicle listings (title, price, mileage, year, detail link).
2. **Email** - Formats results into an HTML email report and sends via SMTP to mohit.sanwal@gmail.com.
3. **Schedule** - GitHub Actions cron job runs daily at 07:00 UTC.

## Setup

### GitHub Secrets Required

| Secret | Description |
|---|---|
| `SMTP_USER` | SMTP username (e.g. your Gmail address) |
| `SMTP_PASS` | SMTP password (Gmail App Password recommended) |

### Optional Secrets

| Secret | Default | Description |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP server port |
