"""
AG.org Church Directory Scraper
================================
Scrapes church listings from:
  https://ag.org/Resources/Directories/Church-Directory?D=25

Output CSV columns:
  church_name, pastor, address, state, zip_code, phone

Usage:
    uv run python ag_church_scraper.py
    uv run python ag_church_scraper.py --url "https://ag.org/Resources/Directories/Church-Directory?D=25"
    uv run python ag_church_scraper.py --headless false --output output/churches.csv
    uv run python ag_church_scraper.py --dump-html   # saves page.html for selector debugging
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ag_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_URL = "https://ag.org/Resources/Directories/Church-Directory?D=25"
DEFAULT_OUTPUT = "ag_churches.csv"
PAGE_LOAD_WAIT = 3      # seconds after initial navigation
RESULTS_TIMEOUT = 30    # selenium explicit-wait timeout (seconds)

# The page is server-side rendered — results are in div.panel > div.church-info
CARD_SELECTOR = "div.church-info"

# Regex to extract state + zip from the end of an address string
# e.g. "6305 Orchard Ln Cincinnati, OH 45213"  →  state="OH", zip="45213"
_STATE_ZIP_RE = re.compile(r",?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

CSV_FIELDS = ("church_name", "pastor", "address", "state", "zip_code", "phone")


@dataclass
class Church:
    church_name: str = ""
    pastor: str = ""
    address: str = ""
    state: str = ""
    zip_code: str = ""
    phone: str = ""

    def is_valid(self) -> bool:
        return bool(self.church_name)


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

def build_driver(headless: bool = True) -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # undetected-chromedriver handles anti-bot patching automatically;
    # headless mode still works but is more detectable — use False for debugging
    return uc.Chrome(options=options, headless=headless, use_subprocess=False, version_main=145)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _text(el) -> str:
    """Stripped inner text of a BS4 element, or empty string."""
    return el.get_text(strip=True) if el else ""


def parse_address(raw: str) -> tuple[str, str, str]:
    """
    Split raw address text into (full_address, state, zip_code).

    '6305 Orchard Ln Cincinnati, OH 45213' → ('...', 'OH', '45213')
    """
    raw = raw.strip()
    m = _STATE_ZIP_RE.search(raw)
    if m:
        return raw, m.group(1), m.group(2)
    return raw, "", ""


def parse_card(info_div) -> Church | None:
    """
    Parse one <div class="church-info"> element.

    HTML structure (from live page):
        <div class="panel">
          <div class="flex grid-md">
            <div class="flex-fill flex-min grid-cell content-formatting">
              <a class="panel-heading" href="...">
                <i class="fas fa-arrow-up"></i>
                <h3>Church Name <br></h3>
              </a>
            </div>
          </div>
          <div class="panel-body">
            <div class="church-info">
              <h4>Pastor Name</h4>
              <p class="address"><i ...></i> Street City, ST ZIP</p>
              <p class="phone"><i ...></i> (555) 555-5555</p>
            </div>
          </div>
        </div>
    """
    c = Church()

    # Church name lives in the sibling .panel-heading, one level up from .church-info
    panel = info_div.find_parent("div", class_="panel")
    if panel:
        h3 = panel.select_one(".panel-heading h3")
        c.church_name = _text(h3)

    # Pastor — <h4> directly inside .church-info
    c.pastor = _text(info_div.find("h4"))

    # Address — <p class="address">; the <i> icon carries no text
    addr_p = info_div.select_one("p.address")
    raw_addr = _text(addr_p)
    c.address, c.state, c.zip_code = parse_address(raw_addr)

    # Phone — <p class="phone">
    phone_p = info_div.select_one("p.phone")
    c.phone = _text(phone_p)

    return c if c.is_valid() else None


def parse_page(driver: uc.Chrome) -> list[Church]:
    """Extract all churches from the currently loaded page."""
    soup = BeautifulSoup(driver.page_source, "lxml")
    info_divs = soup.select(CARD_SELECTOR)
    churches: list[Church] = []
    for div in info_divs:
        church = parse_card(div)
        if church:
            churches.append(church)
    log.info("  -> %d churches on this page", len(churches))
    return churches


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def wait_for_results(driver: uc.Chrome) -> None:
    """
    Block until church-info cards are present on the page.

    The site sits behind Cloudflare, which shows a 'Just a moment...' challenge
    page before rendering real content. We wait for the challenge to clear first,
    then wait for the actual church cards.
    """
    try:
        # Step 1: wait for Cloudflare challenge to clear
        WebDriverWait(driver, RESULTS_TIMEOUT).until(
            lambda d: "just a moment" not in d.title.lower()
        )
    except TimeoutException:
        log.warning("Cloudflare challenge did not clear within %ds.", RESULTS_TIMEOUT)
        return

    try:
        # Step 2: wait for church cards to appear
        WebDriverWait(driver, RESULTS_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SELECTOR))
        )
        time.sleep(0.5)
    except TimeoutException:
        log.warning("Timed out waiting for church cards — page may be empty.")


def get_page_urls(driver: uc.Chrome) -> list[str]:
    """
    Extract all page URLs from the pagination nav on the current page.

    The site renders pagination as:
        <ul class="pagination">
          <li><a href="...?page=1">1</a></li>
          ...
        </ul>

    Returns a list of URLs sorted by page number.
    """
    soup = BeautifulSoup(driver.page_source, "lxml")
    page_re = re.compile(r"[?&]page=(\d+)")
    seen: dict[int, str] = {}

    for a in soup.select("ul.pagination li a"):
        href = a.get("href", "")
        m = page_re.search(href)
        if m:
            seen[int(m.group(1))] = href

    return [seen[n] for n in sorted(seen)]


# ---------------------------------------------------------------------------
# Main scraping loop
# ---------------------------------------------------------------------------

def scrape(url: str, headless: bool, max_pages: int, dump_html: bool) -> list[Church]:
    driver = build_driver(headless=headless)
    all_churches: list[Church] = []

    try:
        log.info("Loading: %s", url)
        driver.get(url)
        time.sleep(PAGE_LOAD_WAIT)
        wait_for_results(driver)

        if dump_html:
            html_path = Path("page.html")
            html_path.write_text(driver.page_source, encoding="utf-8")
            log.info("Page HTML saved to %s", html_path)

        # Collect all page URLs from the pagination on the first page
        page_urls = get_page_urls(driver)
        if page_urls:
            log.info("Found %d pages via pagination", len(page_urls))
        else:
            # Single page — no pagination present
            log.info("No pagination found; scraping single page")
            page_urls = [driver.current_url]

        page_urls = page_urls[:max_pages]

        for page_num, page_url in enumerate(page_urls, start=1):
            log.info("── Page %d / %d ──", page_num, len(page_urls))

            # Page 1 is already loaded; navigate for subsequent pages
            if page_num > 1:
                driver.get(page_url)
                time.sleep(PAGE_LOAD_WAIT)
                wait_for_results(driver)
                log.debug("Current URL after navigation: %s", driver.current_url)
                log.debug("Page title: %s", driver.title)

            churches = parse_page(driver)
            all_churches.extend(churches)

    except Exception:
        log.exception("Fatal error during scraping")
    finally:
        driver.quit()

    log.info("Total churches collected: %d", len(all_churches))
    return all_churches


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_csv(churches: list[Church], output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not churches:
        log.warning("No data to export.")
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(asdict(c) for c in churches)

    log.info("CSV saved: %s (%d rows)", path, len(churches))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape the AG.org Church Directory and export to CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Directory URL to scrape")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output CSV file path")
    parser.add_argument(
        "--headless",
        default="true",
        choices=["true", "false"],
        help="Run Chrome in headless mode",
    )
    parser.add_argument(
        "--max-pages", type=int, default=200, help="Maximum pages to paginate through"
    )
    parser.add_argument(
        "--dump-html",
        action="store_true",
        help="Save the first page HTML to page.html for selector debugging",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    headless = args.headless.lower() == "true"

    log.info("=" * 60)
    log.info("AG.org Church Directory Scraper")
    log.info("URL      : %s", args.url)
    log.info("Output   : %s", args.output)
    log.info("Headless : %s", headless)
    log.info("=" * 60)

    churches = scrape(
        url=args.url,
        headless=headless,
        max_pages=args.max_pages,
        dump_html=args.dump_html,
    )
    export_csv(churches, args.output)


if __name__ == "__main__":
    main()
