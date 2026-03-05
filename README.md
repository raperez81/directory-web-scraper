# AG.org Church Directory Scraper

Scrapes church listings from the [AG.org Church Directory](https://ag.org/Resources/Directories/Church-Directory?D=25) and exports them to a CSV file.

## Output

Each row in the CSV contains:

| Column | Example |
|---|---|
| `church_name` | `21st Century Church` |
| `pastor` | `Reverend Ayodele J Okuwobi` |
| `address` | `6305 Orchard Ln Cincinnati, OH 45213` |
| `state` | `OH` |
| `zip_code` | `45213` |
| `phone` | `(513) 417-3925` |

## Setup

Requires [uv](https://docs.astral.sh/uv/) and **Google Chrome**.

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies and create the virtual environment
uv sync
```

`webdriver-manager` downloads the matching ChromeDriver automatically — no manual setup needed.

## Usage

```bash
# Basic — scrapes the default URL, writes ag_churches.csv
uv run python ag_church_scraper.py

# Custom URL
uv run python ag_church_scraper.py --url "https://ag.org/Resources/Directories/Church-Directory?D=25"

# Custom output path
uv run python ag_church_scraper.py --output output/churches.csv

# Show the browser window (useful for debugging)
uv run python ag_church_scraper.py --headless false

# Limit pages scraped
uv run python ag_church_scraper.py --max-pages 10

# Dump the first page's raw HTML for selector inspection
uv run python ag_church_scraper.py --dump-html
```

### All options

```
options:
  --url URL                 Directory URL to scrape (default: https://ag.org/…?D=25)
  --output OUTPUT           Output CSV file path (default: ag_churches.csv)
  --headless {true,false}   Run browser in headless mode (default: true)
  --max-pages MAX_PAGES     Maximum pages to paginate through (default: 200)
  --dump-html               Save the first page HTML to page.html for debugging
```

## Troubleshooting

**No churches extracted (0 rows)**

The site may have updated its HTML. Run:

```bash
uv run python ag_church_scraper.py --dump-html --headless false
```

Open `page.html`, inspect the class names on church listing elements, then add them to `RESULT_CARD_SELECTORS` at the top of `ag_church_scraper.py`.

**ChromeDriver version mismatch**

Clear the `webdriver-manager` cache and retry:

```bash
rm -rf ~/.wdm
uv run python ag_church_scraper.py
```

## Project structure

```
.
├── ag_church_scraper.py   # Scraper
├── pyproject.toml         # Project metadata & dependencies
├── uv.lock                # Locked dependency versions (commit this)
├── .python-version        # Python version pin (3.11.9)
├── .gitignore
└── README.md
```
