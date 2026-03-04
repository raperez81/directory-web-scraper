"""
Scraper del Directorio de Iglesias de Assemblies of God (ag.org)
================================================================
Descarga información de iglesias del directorio en:
https://ag.org/Resources/Directories/Church-Directory

Requisitos:
    pip install selenium webdriver-manager requests beautifulsoup4 pandas

Uso:
    python ag_church_scraper.py
    python ag_church_scraper.py --state TX          # Filtrar por estado
    python ag_church_scraper.py --zip 65802         # Filtrar por código postal
    python ag_church_scraper.py --headless False    # Ver el navegador en acción
"""

import time
import json
import csv
import argparse
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# ─── Configuración ────────────────────────────────────────────────────────────

BASE_URL = "https://ag.org/Resources/Directories/Church-Directory"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("ag_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── Modelo de datos ──────────────────────────────────────────────────────────

@dataclass
class Church:
    name: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    pastor: str = ""
    service_times: str = ""
    denomination: str = "Assemblies of God"
    extra: dict = field(default_factory=dict)

# ─── Driver ───────────────────────────────────────────────────────────────────

def build_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver

# ─── Parsers de resultados ────────────────────────────────────────────────────

def parse_church_card(card_html: str) -> Optional[Church]:
    """Parsea un bloque HTML de una iglesia individual."""
    soup = BeautifulSoup(card_html, "html.parser")
    church = Church()

    # Nombre (ajustar selectores según el HTML real del sitio)
    name_el = soup.select_one(".church-name, h2, h3, .result-title, [class*='name']")
    if name_el:
        church.name = name_el.get_text(strip=True)

    # Dirección
    addr_el = soup.select_one(".address, [class*='address'], address")
    if addr_el:
        church.address = addr_el.get_text(" ", strip=True)

    # Teléfono
    phone_el = soup.select_one("[href^='tel:'], .phone, [class*='phone']")
    if phone_el:
        church.phone = phone_el.get_text(strip=True).replace("tel:", "")

    # Email
    email_el = soup.select_one("[href^='mailto:']")
    if email_el:
        church.email = email_el.get("href", "").replace("mailto:", "")

    # Sitio web
    web_el = soup.select_one("a[href^='http']:not([href*='ag.org'])")
    if web_el:
        church.website = web_el.get("href", "")

    # Pastor
    pastor_el = soup.select_one(".pastor, [class*='pastor'], [class*='minister']")
    if pastor_el:
        church.pastor = pastor_el.get_text(strip=True)

    return church if church.name else None


def parse_results_page(driver: webdriver.Chrome) -> list[Church]:
    """Extrae todas las iglesias visibles en la página actual."""
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")

    churches = []

    # Selectores comunes — ajustar si el sitio usa otros
    cards = soup.select(
        ".church-result, .directory-result, .search-result, "
        "[class*='church-card'], [class*='result-item'], "
        "li.result, article.church"
    )

    if not cards:
        # Fallback: busca cualquier bloque con nombre de iglesia
        log.warning("No se encontraron tarjetas con selectores estándar. Intentando fallback...")
        cards = soup.select("li, article, .item")

    for card in cards:
        church = parse_church_card(str(card))
        if church:
            churches.append(church)

    log.info(f"  → {len(churches)} iglesias encontradas en esta página")
    return churches


# ─── Navegación y paginación ──────────────────────────────────────────────────

def wait_for_results(driver: webdriver.Chrome, timeout: int = 15):
    """Espera a que los resultados carguen."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR,
                 ".church-result, .directory-result, .search-result, "
                 "[class*='result'], li.result, article")
            )
        )
        time.sleep(1.5)  # margen extra para JS
    except TimeoutException:
        log.warning("Timeout esperando resultados — puede que no haya resultados.")


def apply_filters(driver: webdriver.Chrome, state: str = "", zip_code: str = ""):
    """Aplica filtros de estado y/o código postal si están disponibles."""
    wait = WebDriverWait(driver, 10)

    if state:
        try:
            state_select = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "select[name*='state'], select#state, select[id*='state']")
                )
            )
            Select(state_select).select_by_value(state.upper())
            log.info(f"Filtro de estado aplicado: {state}")
            time.sleep(1)
        except (TimeoutException, NoSuchElementException):
            log.warning("No se encontró selector de estado.")

    if zip_code:
        try:
            zip_input = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                     "input[name*='zip'], input[id*='zip'], input[placeholder*='zip' i]")
                )
            )
            zip_input.clear()
            zip_input.send_keys(zip_code)
            log.info(f"Filtro de código postal aplicado: {zip_code}")
            time.sleep(0.5)
        except (TimeoutException, NoSuchElementException):
            log.warning("No se encontró campo de código postal.")

    # Buscar botón de búsqueda y hacer click
    try:
        search_btn = driver.find_element(
            By.CSS_SELECTOR,
            "button[type='submit'], input[type='submit'], .search-btn, [class*='search-button']"
        )
        search_btn.click()
        log.info("Búsqueda enviada.")
    except NoSuchElementException:
        log.warning("No se encontró botón de búsqueda.")


def go_to_next_page(driver: webdriver.Chrome) -> bool:
    """
    Hace click en 'Siguiente página'. Devuelve True si hubo página siguiente,
    False si era la última.
    """
    try:
        next_btn = driver.find_element(
            By.CSS_SELECTOR,
            "a[rel='next'], .next-page, [aria-label='Next'], "
            "[class*='next']:not([class*='disabled']), li.next a"
        )
        if next_btn and next_btn.is_displayed() and next_btn.is_enabled():
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(2)
            wait_for_results(driver)
            return True
    except NoSuchElementException:
        pass
    return False


# ─── Flujo principal ──────────────────────────────────────────────────────────

def scrape(
    state: str = "",
    zip_code: str = "",
    headless: bool = True,
    max_pages: int = 500,
    output_dir: str = ".",
) -> list[Church]:
    driver = build_driver(headless=headless)
    all_churches: list[Church] = []

    try:
        log.info(f"Abriendo {BASE_URL}")
        driver.get(BASE_URL)
        time.sleep(3)

        # Aplicar filtros si se indicaron
        if state or zip_code:
            apply_filters(driver, state=state, zip_code=zip_code)
            time.sleep(2)

        wait_for_results(driver)

        page = 1
        while page <= max_pages:
            log.info(f"── Página {page} ──")
            churches = parse_results_page(driver)
            all_churches.extend(churches)

            if not go_to_next_page(driver):
                log.info("Última página alcanzada.")
                break
            page += 1

    except Exception as exc:
        log.error(f"Error durante el scraping: {exc}", exc_info=True)
    finally:
        driver.quit()

    return all_churches


# ─── Exportación ──────────────────────────────────────────────────────────────

def export(churches: list[Church], output_dir: str = "."):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not churches:
        log.warning("Sin datos que exportar.")
        return

    # CSV
    csv_path = out / "ag_churches.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=Church.__dataclass_fields__.keys())
        writer.writeheader()
        for c in churches:
            row = asdict(c)
            row.pop("extra", None)          # omitir dict anidado
            writer.writerow(row)
    log.info(f"CSV guardado: {csv_path}")

    # JSON
    json_path = out / "ag_churches.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in churches], f, ensure_ascii=False, indent=2)
    log.info(f"JSON guardado: {json_path}")

    # Excel
    try:
        records = [asdict(c) for c in churches]
        for r in records:
            r.pop("extra", None)
        df = pd.DataFrame(records)
        xlsx_path = out / "ag_churches.xlsx"
        df.to_excel(xlsx_path, index=False, sheet_name="Iglesias AG")
        log.info(f"Excel guardado: {xlsx_path}")
    except Exception as e:
        log.warning(f"No se pudo guardar Excel: {e}")

    log.info(f"\n✅ Total de iglesias descargadas: {len(churches)}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scraper del directorio de iglesias Assemblies of God (ag.org)"
    )
    parser.add_argument("--state",     default="", help="Código de estado (ej: TX, CA, FL)")
    parser.add_argument("--zip",       default="", help="Código postal")
    parser.add_argument("--headless",  default="True", help="True/False — mostrar navegador")
    parser.add_argument("--max-pages", type=int, default=500, help="Máximo de páginas a raspar")
    parser.add_argument("--output",    default=".", help="Directorio de salida")
    args = parser.parse_args()

    headless = args.headless.lower() not in ("false", "0", "no")

    log.info("=" * 60)
    log.info("Scraper de Iglesias AG — ag.org")
    log.info("=" * 60)

    churches = scrape(
        state=args.state,
        zip_code=args.zip,
        headless=headless,
        max_pages=args.max_pages,
        output_dir=args.output,
    )

    export(churches, output_dir=args.output)


if __name__ == "__main__":
    main()
