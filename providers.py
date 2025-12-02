from typing import List, Optional
from models import ProviderResult
import nodriver as uc
from bs4 import BeautifulSoup
import asyncio
import re
import traceback
import base64
import requests
from nodriver import cdp
import aiohttp
import base64
import time

import requests
from bs4 import BeautifulSoup


async def get_rs_session(page) -> requests.Session:
    """
    Extract cookies and user-agent from the browser page and return a configured requests.Session.
    """
    raw_cookies = await page.send(cdp.storage.get_cookies())
    session = requests.Session()
    for c in raw_cookies:
        session.cookies.set(
            c.name,
            c.value
        )

    ua = await page.evaluate("navigator.userAgent")
    session.headers.update({
        "User-Agent": ua,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": "https://us.rs-online.com/",
    })
    return session


from dotenv import load_dotenv
import os
load_dotenv() 

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")


# ────────────────────────────────
# Utility Helpers
# ────────────────────────────────

def parse_price(text: Optional[str]) -> float:
    """Extract a float from strings like '$7.57'."""
    if not text:
        return 0.0
    try:
        cleaned = re.sub(r"[^0-9.]", "", text)
        return float(cleaned) if cleaned else 0.0
    except Exception:
        return 0.0


def parse_int(text: Optional[str]) -> int:
    """Extract an integer from text like 'In Stock: 123'."""
    if not text:
        return 0
    try:
        cleaned = re.sub(r"[^0-9]", "", text)
        return int(cleaned) if cleaned else 0
    except Exception:
        return 0


async def get_soup(page) -> BeautifulSoup:
    """Return BeautifulSoup for current page HTML."""
    html = await page.get_content()
    return BeautifulSoup(html, "html.parser")


async def get_or_create_browser(browser=None):
    """Return an existing browser or create a new one."""
    if browser:
        return browser, False  # existing browser, not owned
    new_browser = await uc.start(
        headless=False,
        no_sandbox=True,
        user_data_dir="/tmp/chrome_profile",
        browser_args=[
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-blink-features=AutomationControlled",
#         "--window-size=1,1",
# "--window-position=-3000,-3000",  # 👈 off-screen (acts like minimized)
    ])
    return new_browser, True  # newly created browser


# ────────────────────────────────
# Digi-Key Scraper
# ────────────────────────────────
async def parse_digikey_product_page(soup, mpn, url):
    results = []

    # Find all pricing blocks
    blocks = soup.find_all("div", {"data-evg": "price-procurement-wrapper"})
    if not blocks:
        return [ProviderResult(
            supplier="DigiKey",
            part_number=mpn,
            manufacturer="N/A",
            stock=0,
            price=0.0,
            url=url,
            exact_match=False
        )]
    manufacturer = soup.find("tr", {"data-testid": "overview-manufacturer"})
    manufacturer_name = manufacturer.text.strip() if manufacturer else None
    for block in blocks:
        stock_span = block.find("span", string=lambda t: t and "In-Stock" in t)
        stock = parse_int(stock_span.text if stock_span else "")

        # Price extraction
        price_table = block.find("table", class_="MuiTable-root")
        prices = []
        if price_table:
            for td in price_table.select("td.MuiTableCell-body:nth-of-type(2)"):
                prices.append(parse_price(td.text))

        price = min(prices) if prices else 0.0

        results.append(
            ProviderResult(
                supplier="DigiKey",
                part_number=mpn,
                manufacturer=manufacturer_name,
                stock=stock,
                price=price,
                url=url,
                exact_match=True,
            )
        )

    return results


async def scrape_digikey(mpn: str, browser=None) -> List[ProviderResult]:
    base_url = "https://www.digikey.com"
    search_url = f"{base_url}/en/products/result?keywords={mpn}"

    browser, own_browser = await get_or_create_browser(browser)
    results = []

    try:
        # ---------------------------
        # LOAD SEARCH PAGE
        # ---------------------------
        page = await browser.get(search_url)
        await asyncio.sleep(15)
        soup = await get_soup(page)

        # ---------------------------
        # CASE 1: DIRECT PRODUCT PAGE
        # ---------------------------
        # If we are already on a product page, Digi-Key prints the MPN in a data attribute.
        product_header = soup.find("div", {"data-evg": "price-procurement-wrapper"})
        if product_header:
            return await parse_digikey_product_page(soup, mpn, search_url)

        # ---------------------------
        # CASE 2: EXACT MATCH BANNER
        # ---------------------------
        exact_match_block = soup.find("div", {"data-testid": "category-exact-match"})
        if exact_match_block:
            link = exact_match_block.find("a", href=True)
            if link:
                url = base_url + link["href"]
                page = await browser.get(url)
                await asyncio.sleep(15)
                soup = await get_soup(page)
                return await parse_digikey_product_page(soup, mpn, url)

        # ---------------------------
        # CASE 3: LIST PAGE (multiple rows)
        # ---------------------------
        rows = soup.select("div[data-testid='sb-content-container'] tbody tr")
        if rows:
            for row in rows:
                sku_block = row.find("div", class_=re.compile("mfrProdNumHeader"))
                if not sku_block:
                    continue

                scraped_sku = sku_block.get_text(strip=True)

                # Found an exact part in the list
                # mpn= "ST201M-C5"
                if scraped_sku.lower() == mpn.lower():
                    link = sku_block.find("a", href=True)
                    if link:
                        url = base_url + link["href"]

                        page = await browser.get(url)
                        await asyncio.sleep(15)
                        soup = await get_soup(page)
                        return await parse_digikey_product_page(soup, mpn, url)

        # ---------------------------
        # NOTHING FOUND
        # ---------------------------
        results.append(
            ProviderResult(
                supplier="DigiKey",
                part_number=mpn,
                manufacturer="N/A",
                stock=0,
                price=0.0,
                url="Not Found",
                exact_match=False,
            )
        )
        return results

    except Exception as e:
        print(f"[ERROR] DigiKey {mpn}: {e}")
        traceback.print_exc()
        return []

    finally:
        if own_browser:
            browser.stop()


# async def scrape_digikey(mpn: str, browser=None) -> List[ProviderResult]:
#     url = f"https://www.digikey.com/en/products/result?keywords={mpn}"
#     browser, own_browser = await get_or_create_browser(browser)
#     results: List[ProviderResult] = []

#     try:
#         page = await browser.get(url)
#         await asyncio.sleep(5)
#         await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
#         await asyncio.sleep(2)
#         soup = await get_soup(page)

#         # Three options to find the Product: direct match, multiple results, exact match

#         # multiple results case
#         try:
#             products_container = soup.find("div", {"data-testid":"sb-content-container"}).find("tbody").find_all("tr")
#             for prod in products_container:
#                 scraped_sku_con = prod.find("div", class_=re.compile(r"mfrProdNumHeader"))
#                 scraped_sku = scraped_sku_con.text.strip().lower()
#                 if scraped_sku == mpn.lower():
#                     exact_match = True
#                     new_link = scraped_sku_con.find("a", href=True)
#                     if new_link:
#                         product_url = f"https://www.digikey.com{new_link['href']}"
                        
#                         page = await browser.get(product_url)
#                         await asyncio.sleep(5)
#                         await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
#                         await asyncio.sleep(2)
#                         soup = await get_soup(page)
#                         break
#         except Exception:
#             pass  # proceed to check for direct match or exact match

#         # direct match or exact match case
#         try:
#             product_con = soup.find("div", {"data-testid":"category-exact-match"})
#             new_link = product_con.find("a", href=True)
#             if new_link:
#                 product_url = f"https://www.digikey.com{new_link['href']}"
                
#                 page = await browser.get(product_url)
#                 await asyncio.sleep(5)
#                 await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
#                 await asyncio.sleep(2)
#                 soup = await get_soup(page)
#         except Exception:
#             pass  # proceed to parse whatever page we have
        
#         divs = soup.find_all("div", {"data-evg": "price-procurement-wrapper"})
#         if not divs:
#             print("⚠️ Digi-Key: No products found, retrying once.")
#             results.append(ProviderResult(
#                 supplier="DigiKey",
#                 part_number=mpn,
#                 manufacturer="N/A",
#                 stock=0,
#                 price=0.0,
#                 url="Not Found"
#             ))
#             await page.reload()
#             await asyncio.sleep(3)
#             soup = await get_soup(page)
#             divs = soup.find_all("div", {"data-evg": "price-procurement-wrapper"})
#             if not divs:
#                 return results

#         for div in divs:
#             stock_span = div.find("span", string=lambda t: t and "In-Stock" in t)
#             stock = parse_int(stock_span.text) if stock_span else 0

#             table = div.select_one("table.MuiTable-root")
#             unit_prices = [parse_price(td.text) for td in table.select("td.MuiTableCell-body:nth-of-type(2)")] if table else []
#             price = min(unit_prices) if unit_prices else 0.0

#             link_tag = div.find("a", href=True)
#             product_url = f"https://www.digikey.com{link_tag['href']}" if link_tag else url

#             # results.append(ProviderResult("DigiKey", mpn, None, stock, price, product_url))
#             results.append(ProviderResult(
#                 supplier="DigiKey",
#                 part_number=mpn,
#                 manufacturer=None,
#                 stock=stock,
#                 price=price,
#                 url=product_url, 
#                 exact_match= True
#             ))

#         print(f"✅ Digi-Key scrape complete for {mpn}, found {len(results)} products.")
#         return results

#     except Exception as e:
#         print(f"[ERROR] Digi-Key exception for {mpn}: {e}")
#         traceback.print_exc()
#         return []

#     finally:
#         if own_browser:
#             browser.stop()


# ────────────────────────────────
# Galco Scraper
# ────────────────────────────────
async def scrape_galco(mpn: str, brand: str, browser=None, _retry=False) -> List[ProviderResult]:
    base_url = "https://www.galco.com"
    search_url = f"{base_url}/catalogsearch/result/?q={mpn}"

    browser, own_browser = await get_or_create_browser(browser)
    results = []

    try:
        # ---------------------------
        # LOAD SEARCH PAGE
        # ---------------------------
        page = await browser.get(search_url)
        await asyncio.sleep(15)
        soup = await get_soup(page)

        # ---------------------------
        # CASE 1: NO RESULTS
        # ---------------------------
        if soup.find("div", class_="no-results"):
            return [
                ProviderResult(
                    supplier="Galco",
                    part_number=mpn,
                    manufacturer="N/A",
                    stock=0,
                    price=0.0,
                    url="Not Found",
                    exact_match=False
                )
            ]

        # ---------------------------
        # CASE 2: PRODUCT PAGE DIRECT (single product)
        # ---------------------------
        if soup.find("div", class_="product-info-main"):  # Galco product pages have this
            return await parse_galco_product_page(soup, mpn, brand, page.url)

        # ---------------------------
        # CASE 3: SEARCH RESULTS LIST
        # ---------------------------
        product_cards = soup.find_all("div", class_="product main-details")
        # if it product_cards is empty
        
        if product_cards == [] and  not _retry:
            print(f"[Galco] No results for {mpn}, RETRYING...")
            browser.stop()
            return await scrape_galco(mpn, brand, browser=None, _retry=True)

        if not product_cards:
            # No results even after checking — return empty set
            return [
                ProviderResult(
                    supplier="Galco",
                    part_number=mpn,
                    manufacturer="N/A",
                    stock=0,
                    price=0.0,
                    url="Not Found",
                    exact_match=False
                )
            ]

        # Look for matching MPN in the results
        for card in product_cards:
            brand_el = card.find("div", class_="product attribute brand")
            scraped_brand = brand_el.text.strip() if brand_el else ""

            mpn_tag = card.find("div", class_="mfg-item-number")
            scraped_mpn = (
                mpn_tag.find("div", class_="value").get_text(strip=True)
                if mpn_tag else ""
            )

            if scraped_mpn.lower() == mpn.lower():
                # Navigate into product page
                link = card.find("a", class_="product-item-link", href=True)
                if link:
                    product_url = base_url + link["href"]
                    page = await browser.get(product_url)
                    await asyncio.sleep(2)
                    product_soup = await get_soup(page)

                    return await parse_galco_product_page(
                        product_soup, mpn, scraped_brand, product_url
                    )

        # ---------------------------
        # CASE 4: MPN NOT FOUND IN LIST
        # ---------------------------
        return [
            ProviderResult(
                supplier="Galco",
                part_number=mpn,
                manufacturer="N/A",
                stock=0,
                price=0.0,
                url=search_url,
                exact_match=False,
            )
        ]

    except Exception as e:
        print(f"[ERROR] Galco exception for {mpn}: {e}")
        traceback.print_exc()
        return []

    finally:
        if own_browser:
            browser.stop()

async def parse_galco_product_page(soup, mpn, brand, url):
    results = []

    # Stock
    stock_el = soup.select_one("span.stock-number")
    stock = parse_int(stock_el.text if stock_el else "")

    # Price
    price_el = soup.select_one("span.price")
    price = parse_price(price_el.text if price_el else "")

    return [
        ProviderResult(
            supplier="Galco",
            part_number=mpn,
            manufacturer=brand,
            stock=stock,
            price=price,
            url=url,
            exact_match=True
        )
    ]

# async def scrape_galco(mpn: str, brand: str, browser=None) -> List[ProviderResult]:
#     url = f"https://www.galco.com/catalogsearch/result/?q={mpn}"
#     browser, own_browser = await get_or_create_browser(browser)
#     results: List[ProviderResult] = []

#     try:
#         page = await browser.get(url)
#         await asyncio.sleep(5)
#         await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
#         await asyncio.sleep(2)
#         soup = await get_soup(page)

#         if soup.find("div", class_="no-results"):
#             print("⚠️ Galco: No products found.")
#             results.append(ProviderResult(supplier="Galco",part_number= mpn, manufacturer="N/A", stock=0, price=0.0, url="Not Found"))
#             return results

#         products = soup.find_all("div", class_="product main-details")
#         if not products:
#             print("⚠️ Galco: Retrying after no results.")
#             results.append(ProviderResult(supplier="Galco", part_number=mpn, manufacturer="N/A", stock=0, price=0.0, url="Not Found"))
#             await page.reload()
#             await asyncio.sleep(3)
#             soup = await get_soup(page)
#             products = soup.find_all("div", class_="product main-details")
#             if not products:
#                 return results

#         for prod in products:
#             brand_name = (prod.find("div", class_="product attribute brand").text.strip().lower()
#                           if prod.find("div", class_="product attribute brand") else "")
#             mpn_tag = prod.find("div", class_="mfg-item-number")
#             mpn_text = mpn_tag.find("div", class_="value").text.strip() if mpn_tag else ""

#             if mpn_text == mpn:
#                 exact_match = True
#                 link_tag = prod.find("a", class_="product-item-link", href=True)
#                 product_url = f"https://www.galco.com{link_tag['href']}" if link_tag else url
#                 price = parse_price(prod.select_one("span.price").text if prod.select_one("span.price") else "")
#                 stock = parse_int(prod.select_one("span.stock-number").text if prod.select_one("span.stock-number") else "")
#                 results.append(ProviderResult(supplier="Galco",part_number= mpn_text, manufacturer=brand_name, stock=stock, price=price, url=product_url, exact_match= exact_match ))
#                 break

#         print(f"✅ Galco scrape complete for {mpn}, found {len(results)} products.")
#         return results

#     except Exception as e:
#         print(f"[ERROR] Galco exception for {mpn}: {e}")
#         traceback.print_exc()
#         return []

#     finally:
#         if own_browser:
#             browser.stop()

# -----------------------------
# Async scrape function
# -----------------------------
# -----------------------------
# Get RS session dynamically
# -----------------------------
# async def get_rs_session_dynamic():
#     """
#     Open RS Online in a browser, extract cookies + user-agent, return requests.Session
#     """
#     browser = await uc.start(headless=False)
#     page = await browser.get("https://us.rs-online.com")
#     await asyncio.sleep(5)  # wait for cookies to load

#     raw_cookies = await page.send(cdp.storage.get_cookies())
#     ua = await page.evaluate("navigator.userAgent")

#     session = requests.Session()
#     session.headers.update({
#         'accept': 'application/json, text/plain, */*',
#         'accept-language': 'en-US,en;q=0.9',
#         'referer': 'https://us.rs-online.com/',
#         'user-agent': ua,
#         'x-requested-with': 'XMLHttpRequest',
#     })

#     for c in raw_cookies:
#         session.cookies.set(c.name, c.value)

#     browser.stop()
#     return session
async def get_rs_session_with_datadome(existing_cookies: dict, headers: dict) -> requests.Session:
    """
    Open RS Online in a browser, extract the datadome cookie, 
    and return a requests.Session with all other existing cookies + datadome.
    """
    browser = await uc.start(headless=False)
    page = await browser.get("https://us.rs-online.com")
    await asyncio.sleep(5)  # wait for cookies to populate

    # Extract cookies from browser
    time.sleep(5)
    raw_cookies = await page.send(cdp.storage.get_cookies())
    datadome_value = None
    for c in raw_cookies:
        if c.name.lower() == "datadome":
            datadome_value = c.value
            break

    browser.stop()

    # Create session
    session = requests.Session()
    session.headers.update(headers)

    # Copy existing cookies, override datadome if found
    for name, value in existing_cookies.items():
        session.cookies.set(name, value)
    if datadome_value:
        session.cookies.set("datadome", datadome_value)

    return session

# -----------------------------
# Example usage
# -----------------------------
existing_cookies = {
    'envMode': 'Live Mode',
    'form_key': 'dM2rxdGorP9qzaFz',
    'mage-cache-storage': '{}',
    'mage-cache-storage-section-invalidation': '{}',
    'mage-messages': '',
    'recently_viewed_product': '{}',
    'recently_viewed_product_previous': '{}',
    'recently_compared_product': '{}',
    'recently_compared_product_previous': '{}',
    'product_data_storage': '{}',
    'wp_ga4_customerGroup': 'NOT%20LOGGED%20IN',
    'PHPSESSID': 'ef5dc0c47e68404545c7903771c90e73',
    # 'section_data_ids': '{...}',  # trimmed for brevity
    'gbi_visitorId': 'a451fef4d47e9a3801635d232e558590',
    # 'datadome' will be replaced dynamically
}

headers = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.7',
    'referer': 'https://us.rs-online.com/catalogsearch/result/?q=schneider+electric',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'x-requested-with': 'XMLHttpRequest',
    # other headers as needed
}

# -----------------------------
# Check if MPN exists in title chunks
# -----------------------------
def title_matches_mpn(title: str, mpn: str) -> bool:
    mpn_lower = mpn.lower()
    chunks = [chunk.strip(" ,-/()").lower() for chunk in title.split()]
    return mpn_lower in chunks

# -----------------------------
# RS scraper using dynamic session
# -----------------------------
async def scrape_rs(mpn: str, page_size: int = 20, max_pages: int = 3) -> List[ProviderResult]:
    session = await get_rs_session_with_datadome(existing_cookies, headers)
    results: List[ProviderResult] = []
    base_endpoint = "https://us.rs-online.com/groupby/search/endpoint"

    try:
        for page_num in range(1, max_pages + 1):
            params = {
                'page': str(page_num),
                'page_size': str(page_size),
                'query': mpn,
                'in_stock': '0',
            }
            session.headers.update({
                'referer': f'https://us.rs-online.com/catalogsearch/result/?q={mpn}&page={page_num}'
            })

            resp = session.get(base_endpoint, params=params)
            resp.raise_for_status()
            data = resp.json()

            products = data.get('records', [])
            if not products:
                continue

            for prod in products:
                allMeta = prod.get('allMeta', {})
                title = allMeta.get('title', '')
                attributes = allMeta.get('attributes', {})
                attr_mpn_list = attributes.get('manufacturer_part_number', {}).get('text', [])
                attr_mpn = attr_mpn_list[0] if attr_mpn_list else ''

                # Skip if MPN doesn't match in title or attributes
                if mpn.lower() not in attr_mpn.lower():
                    continue

                price_info = allMeta.get('priceInfo', {})
                stock = allMeta.get('attributes', {}).get('available_qty', {}).get('numbers', [0])[0]

                results.append(
                    ProviderResult(
                        supplier="RS Electric",
                        part_number=mpn,
                        manufacturer=", ".join(allMeta.get('brands', [])) or 'N/A',
                        stock=int(stock),
                        price=float(price_info.get('price', 0.0)),
                        url=allMeta.get('uri', f'https://us.rs-online.com/catalogsearch/result/?q={mpn}'),
                        exact_match=True
                    )
                )
                break
            if results:
                break  # Stop if we found results
            

            

        if not results:
            results.append(
                ProviderResult(
                    supplier="RS Electric",
                    part_number=mpn,
                    manufacturer="N/A",
                    stock=0,
                    price=0.0,
                    url=f'https://us.rs-online.com/catalogsearch/result/?q={mpn}',
                    exact_match=False
                )
            )

        return results

    except Exception as e:
        print(f"[ERROR] RS scraper exception for {mpn}: {e}")
        return []
# async def scrape_rs(mpn: str, browser=None, wait_per_try: int = 5) -> List[ProviderResult]:
#     base_url = "https://us.rs-online.com"
#     search_url = f"{base_url}/catalogsearch/result/?q={mpn}&page=1"

#     browser, own_browser = await get_or_create_browser(browser)
#     results = []

#     try:
#         # ---------------------------
#         # LOAD SEARCH PAGE
#         # ---------------------------
#         page = await browser.get(search_url)
#         await asyncio.sleep(wait_per_try)
#         await asyncio.sleep(15)
#         soup = await get_soup(page)

#         # ---------------------------
#         # CASE 1: DIRECT PRODUCT PAGE
#         # ---------------------------
#         if soup.find("div", id="productDetails"):  # RS uses this on product pages
#             return await parse_rs_product_page(soup, mpn, page.url)

#         # ---------------------------
#         # CASE 2: SEARCH RESULTS TABLE
#         # ---------------------------
#         rows = soup.find_all("tr", class_="product-item")

#         if not rows:
#             # Reload once
#             await page.reload()
#             await asyncio.sleep(wait_per_try)
#             soup = await get_soup(page)
#             rows = soup.find_all("tr", class_="product-item")

#             if not rows:
#                 return [
#                     ProviderResult(
#                         supplier="RS Electric",
#                         part_number=mpn,
#                         manufacturer="N/A",
#                         stock=0,
#                         price=0.0,
#                         url="Not Found",
#                         exact_match=False
#                     )
#                 ]

#         # ---------------------------
#         # FIND EXACT MATCH IN LIST
#         # ---------------------------
#         for row in rows:
#             # URL
#             link_tag = row.find("strong", class_="product-item-name").find("a", href=True)
#             product_url = link_tag["href"] if link_tag else search_url

#             # Manufacturer part number
#             details = row.find("div", class_="product-item-details")
            
#             scraped_mpn = ""

#             for p in details.find_all("p"):
#                 if "Manufacturer Part" in p.get_text():
#                     scraped_mpn = p.get_text().split(":")[1].split(",")[0].strip()
#                     break

#             if scraped_mpn.lower() != mpn.lower():
#                 continue

#             # Navigate into product page for details
#             page = await browser.get(product_url)
#             await asyncio.sleep(15)
#             product_soup = await get_soup(page)

#             return await parse_rs_product_page(product_soup, scraped_mpn, product_url)

#         # ---------------------------
#         # NO EXACT MATCH FOUND
#         # ---------------------------
#         return [
#             ProviderResult(
#                 supplier="RS Electric",
#                 part_number=mpn,
#                 manufacturer="N/A",
#                 stock=0,
#                 price=0.0,
#                 url=search_url,
#                 exact_match=False
#             )
#         ]

#     except Exception as e:
#         print(f"[ERROR] RS exception for {mpn}: {e}")
#         traceback.print_exc()
#         return []

#     finally:
#         if own_browser:
#             browser.stop()

async def parse_rs_product_page(soup, mpn, url):
    # Stock
    stock_el = soup.find(class_="stock available")
    stock = parse_int(stock_el.text if stock_el else "")

    # Price
    price_el = soup.select_one(".price-box.price-final_price")
    price = parse_price(price_el.text if price_el else "")
    
    manufacturer = None
    # data-th="Brand"
    brand = soup.find("td", attrs={"data-th": "Brand"})
    if brand:
        manufacturer = brand.text.strip()
    else:
        manufacturer = None
    
    return [
        ProviderResult(
            supplier="RS Electric",
            part_number=mpn,
            manufacturer=None,
            stock=stock,
            price=price,
            url=url,
            exact_match=True
        )
    ]

# async def scrape_rs(mpn: str, browser=None, wait_per_try: int = 5) -> List[ProviderResult]:
#     url = f"https://us.rs-online.com/catalogsearch/result/?q={mpn}&page=1"
#     browser, own_browser = await get_or_create_browser(browser)
#     results: List[ProviderResult] = []

#     try:
#         page = await browser.get(url)
#         await asyncio.sleep(wait_per_try)
#         await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
#         await asyncio.sleep(2)
#         soup = await get_soup(page)
#         rows = soup.find_all("tr", class_="product-item")

#         if not rows:
#             print("⚠️ RS: Retrying after no results.")
#             results.append(ProviderResult(supplier="RS Electric", part_number=mpn, manufacturer="N/A", stock=0, price=0.0, url= "Not Found"))
#             await page.reload()
#             await asyncio.sleep(3)
#             soup = await get_soup(page)
#             rows = soup.find_all("tr", class_="product-item")
#             if not rows:
#                 return results

#         for row in rows:
#             link_tag = row.find("a", class_="product-item-name", href=True)
#             product_url = link_tag["href"] if link_tag else url

#             details = row.find("div", "product-item-details")
#             mpn_text = ""
#             for p in details.find_all("p"):
#                 if "Manufacturer Part" in p.get_text():
                
#                     mpn_text = p.get_text().split(":")[1].split(",")[0].strip()
#                     break

#             stock = parse_int(row.select_one(".stock-badge__detail-number").text if row.select_one(".stock-badge__detail-number") else "")
#             price = parse_price(row.select_one(".product-price span[data-currency-code='USD']").text
#                                 if row.select_one(".product-price span[data-currency-code='USD']") else "")

#             if mpn_text.lower() == mpn.lower():
#                 exact_match = True
#                 results.append(ProviderResult(supplier="RS Electric", part_number=mpn_text,manufacturer= None, stock=stock, price=price,url= product_url, exact_match= exact_match ))
#                 break

#         print(f"✅ RS scrape complete for {mpn}, found {len(results)} products.")
#         return results

#     except Exception as e:
#         print(f"[ERROR] RS exception for {mpn}: {e}")
#         traceback.print_exc()
#         return []

#     finally:
#         if own_browser:
#             browser.stop()


# ────────────────────────────────
# Mouser Scraper
# ────────────────────────────────
async def scrape_mouser(mpn: str, browser=None, wait_per_try: int = 5) -> List[ProviderResult]:
    search_url = f"https://www.mouser.com/c/?q={mpn}"
    print(f"🔍 Searching Mouser for: {mpn}")

    browser, own_browser = await get_or_create_browser(browser)
    results = []

    try:
        # ---------------------------
        # LOAD SEARCH PAGE
        # ---------------------------
        page = await browser.get(search_url)
        await asyncio.sleep(wait_per_try)
        await asyncio.sleep(15)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        soup = await get_soup(page)

        # ---------------------------
        # CASE 1: DIRECT PRODUCT PAGE
        # ---------------------------
        if soup.find("div", id="pdpPricingAvailability"):
            return await parse_mouser_product_page(soup, mpn, search_url)

        # ---------------------------
        # CASE 2: SEARCH RESULTS LIST
        # ---------------------------
        rows = soup.find_all("tr", attrs={"data-partnumber": True})

        if not rows:
            # Retry once
            await page.reload()
            await asyncio.sleep(wait_per_try)
            await asyncio.sleep(15)
            soup = await get_soup(page)
            rows = soup.find_all("tr", attrs={"data-partnumber": True})

            if not rows:
                return [
                    ProviderResult(
                        supplier="Mouser",
                        part_number=mpn,
                        manufacturer="N/A",
                        stock=0,
                        price=0.0,
                        url="Not Found",
                        exact_match=False
                    )
                ]

        # ---------------------------
        # FIND EXACT MATCH IN LIST PAGE
        # ---------------------------
        for row in rows:
            sku_tag = row.find("div", class_="mfr-part-num")
            if not sku_tag:
                continue

            scraped_sku = sku_tag.get_text(strip=True).lower()
            # remove mfr. part # prefix if present
            scraped_sku = re.sub(r"^mfr\. part #\s*", "", scraped_sku)

            if scraped_sku != mpn.lower():
                continue

            # Found exact match — parse table
            # find the link and send to be extracted
            link_tag = sku_tag.find("a", href=True) 
            product_url = link_tag["href"] if link_tag else search_url
            if not product_url.startswith("http"):
                product_url = "https://www.mouser.com" + product_url
            page = await browser.get(product_url)
            await asyncio.sleep(15)
            product_soup = await get_soup(page)
            return await parse_mouser_product_page(product_soup, mpn, product_url)
        
            # stock = parse_int(row.select_one(".available-amount").text
            #                   if row.select_one(".available-amount") else "")
            # price = parse_price(row.select_one("td.PriceBreakPrice span").text
            #                     if row.select_one("td.PriceBreakPrice span") else "")

            # return [
            #     ProviderResult(
            #         supplier="Mouser",
            #         part_number=mpn,
            #         manufacturer=None,
            #         stock=stock,
            #         price=price,
            #         url=search_url,
            #         exact_match=True,
            #     )
            # ]

        # ---------------------------
        # NO EXACT MATCH FOUND
        # ---------------------------
        return [
            ProviderResult(
                supplier="Mouser",
                part_number=mpn,
                manufacturer=None,
                stock=0,
                price=0.0,
                url=search_url,
                exact_match=False,
            )
        ]

    except Exception as e:
        print(f"[ERROR] Mouser exception for {mpn}: {e}")
        traceback.print_exc()
        return []

    finally:
        if own_browser:
            browser.stop()

async def parse_mouser_product_page(soup, mpn, url):
    # Restricted availability?
    restricted_el = soup.find(attrs={"data-testid": "RestrictedAvailabilityTrigger"})
    if restricted_el and "Restricted Availability" in restricted_el.text:
        return [
            ProviderResult(
                supplier="Mouser",
                part_number=mpn,
                manufacturer="N/A",
                stock=0,
                price=0.0,
                url="Not Found",
                exact_match=False,
            )
        ]

    # Extract SKU
    # Manufacturer
    # id = lnkManufacturerName
    manufacturer_el = soup.find("a", id="lnkManufacturerName")
    manufacturer = manufacturer_el.get_text(strip=True) if manufacturer_el else None
    sku_el = soup.find("span", id="spnManufacturerPartNumber")
    scraped_sku = sku_el.get_text(strip=True) if sku_el else ""

    exact_match = scraped_sku.lower() == mpn.lower()

    # Stock
    stock_el = soup.find("h2", {"data-testid": "PricingAvailabilityHeader"})
    stock = parse_int(stock_el.text if stock_el else "")

    # Price (first price break row)
    row = soup.find("tr", {"data-testid": "PricingTablePriceBreakRow"})
    price = parse_price(row.find_all("td")[0].text if row else "")

    return [
        ProviderResult(
            supplier="Mouser",
            part_number=scraped_sku or mpn,
            manufacturer=manufacturer,
            stock=stock,
            price=price,
            url=url,
            exact_match=exact_match,
        )
    ]

# async def scrape_mouser(mpn: str, browser=None, wait_per_try: int = 5) -> List[ProviderResult]:
#     url = f"https://www.mouser.com/c/?q={mpn}"
#     print(f"🔍 Searching Mouser for: {mpn}")
#     browser, own_browser = await get_or_create_browser(browser)
#     results: List[ProviderResult] = []

#     try:
#         page = await browser.get(url)
#         await asyncio.sleep(wait_per_try)
#         await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
#         await asyncio.sleep(2)
#         soup = await get_soup(page)
#         rows = soup.find_all("tr", attrs={"data-partnumber": True})
#         price_container = soup.find("div", id="pdpPricingAvailability")

#         if price_container:
#             status_string = soup.find(attrs={"data-testid": "RestrictedAvailabilityTrigger"})
#             if status_string and "Restricted Availability" in status_string.text:
#                 print("⚠️ Mouser: Restricted Availability.")
#                 results.append(ProviderResult(supplier="Mouser",part_number= mpn, manufacturer="N/A",stock= 0, price=0.0, url="Not Found"))
#                 return results

#             # Check the Sku scraped
            
#             try:
#                 tag = soup.find("span", id="spnManufacturerPartNumber")
#                 scraped_sku = tag.get_text(strip=True) if tag else None
#                 if scraped_sku.lower() == mpn.lower():
#                     exact_match = True
#                 else:
#                     exact_match = False
#             except Exception:   
#                 exact_match = False

#             stock_header = soup.find("h2", {"data-testid": "PricingAvailabilityHeader"})
#             stock = parse_int(stock_header.text if stock_header else "")
#             first_row = soup.find("tr", {"data-testid": "PricingTablePriceBreakRow"})
#             first_price = parse_price(first_row.find_all("td")[0].text if first_row else "")
#             results.append(ProviderResult(supplier="Mouser",part_number= mpn, manufacturer=None, stock=stock,price= first_price, url=url, exact_match= exact_match ))
#             return results

#         if not rows:
#             print("⚠️ Mouser: Retrying after no results.")
#             results.append(ProviderResult(supplier="Mouser",part_number= mpn,manufacturer= "N/A",stock= 0,price= 0.0,url= "Not Found"))
#             await page.reload()
#             await asyncio.sleep(3)
#             soup = await get_soup(page)
#             rows = soup.find_all("tr", attrs={"data-partnumber": True})
#             if not rows:
#                 return results

#         for row in rows:
#             check_sku = row.find("div", class_="mfr-part-num").find("a").text.strip().lower()
#             if check_sku == mpn.lower():
#                 exact_match = True
#                 stock = parse_int(row.select_one(".available-amount").text if row.select_one(".available-amount") else "")
#                 price = parse_price(row.select_one("td.PriceBreakPrice span").text if row.select_one("td.PriceBreakPrice span") else "")
#                 results.append(ProviderResult(supplier="Mouser",part_number= mpn,manufacturer= None,stock= stock,price= price,url= url, exact_match= exact_match ))
#                 break

#         print(f"✅ Mouser scrape complete for {mpn}, found {len(results)} products.")
#         return results

#     except Exception as e:
#         print(f"[ERROR] Mouser exception for {mpn}: {e}")
#         traceback.print_exc()
#         return []

#     finally:
#         if own_browser:
#             browser.stop()

# ────────────────────────────────
# eBay API Scraper (FULL ASYNC)
# ────────────────────────────────


_cached_token = None
_cached_token_expire = 0


async def ebay_get_access_token():
    global _cached_token, _cached_token_expire

    # Reuse existing token
    if _cached_token and time.time() < _cached_token_expire:
        return _cached_token

    creds = f"{CLIENT_ID}:{CLIENT_SECRET}"
    encoded = base64.b64encode(creds.encode()).decode()

    url = "https://api.ebay.com/identity/v1/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {encoded}"}
    data = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data) as resp:
            resp.raise_for_status()
            payload = await resp.json()
            _cached_token = payload["access_token"]
            _cached_token_expire = time.time() + payload.get("expires_in", 3600)
            return _cached_token


def extract_sku_tokens(title: str):
    return re.findall(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*", title)


async def scrape_ebay(mpn: str) -> List[ProviderResult]:
    print(f"🔍 Searching eBay for {mpn}...")

    token = await ebay_get_access_token()

    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {
        "q": f'"{mpn}"',
        "limit": "50",
        "filter": "conditionIds:{1000},itemLocationCountry:US"
    }

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

    items = data.get("itemSummaries", [])
    results = []
    mpn_upper = mpn.upper()

    for item in items:
        title = item.get("title", "")
        if not title:
            continue

        tokens = extract_sku_tokens(title.upper())

        # find the best matching SKU token
        scraped_sku = None
        exact_match = False

        for t in tokens:
            if mpn_upper in t:
                if t == mpn_upper:
                    exact_match = True
                scraped_sku = t
                break
        
        # If no exact match, still capture the FIRST token as "closest"
        # if not scraped_sku and tokens:
        #     scraped_sku = tokens[0]

        # must have image
        if not item.get("image", {}).get("imageUrl"):
            continue

        # seller feedback ≥ 98%
        feedback = float(item.get("seller", {}).get("feedbackPercentage", 0))
        if feedback < 98:
            continue

        # returns accepted
        if item.get("returnTerms", {}).get("returnsAccepted") is False:
            continue

        price = float(item.get("price", {}).get("value", 0.0))
        url = item.get("itemWebUrl", "")

        results.append(ProviderResult(
            supplier="eBay",
            part_number=mpn,
            manufacturer=None,
            stock=0,
            price=price,
            url=url,
            exact_match=exact_match,
            scraped_sku=scraped_sku   # NEW FIELD
        ))

    print(f"✅ eBay done: {len(results)} results, :result 1: {results[0] if results else 'N/A'}")
    return results

# ───────────────────────────────
# Radwell Scraper
# ───────────────────────────────
async def scrape_radwell(mpn: str, browser=None, wait_per_try: int = 5) -> List[ProviderResult]:
    base_url = "https://www.radwell.com"
    search_url = f"{base_url}/Search/?q={mpn}"

    print(f"🔍 Searching Radwell for: {mpn}")

    browser, own_browser = await get_or_create_browser(browser)
    results = []

    try:
        # ---------------------------
        # LOAD SEARCH PAGE
        # ---------------------------
        page = await browser.get(search_url)
        await asyncio.sleep(wait_per_try)
        await asyncio.sleep(15)
        soup = await get_soup(page)

        # ---------------------------
        # CASE 1: DIRECT PRODUCT PAGE
        # ---------------------------
        if soup.find("div", class_="rd-buyOpts"):
            return await parse_radwell_product_page(soup, mpn, page.url)

        # ---------------------------
        # CASE 2: SEARCH RESULTS LIST
        # ---------------------------
        results_div = soup.find(id="searchResults")

        if not results_div:
            # Retry once
            await page.reload()
            await asyncio.sleep(wait_per_try)
            soup = await get_soup(page)
            results_div = soup.find(id="searchResults")

            if not results_div:
                return [
                    ProviderResult(
                        supplier="Radwell",
                        part_number=mpn,
                        manufacturer="N/A",
                        stock=0,
                        price=0.0,
                        url="Not Found",
                        exact_match=False
                    )
                ]

        # Find item tiles
        items = results_div.find_all("a", class_="taglink")

        if not items:
            return [
                ProviderResult(
                    supplier="Radwell",
                    part_number=mpn,
                    manufacturer="N/A",
                    stock=0,
                    price=0.0,
                    url="Not Found",
                    exact_match=False
                )
            ]

        # ---------------------------
        # FIND EXACT MATCH IN SEARCH RESULTS
        # ---------------------------
        for item in items:
            title_tag = item.find("div", class_="partno")
            title = title_tag.get("title", "").strip()
            scraped_sku = title.lower()

            if scraped_sku != mpn.lower():
                continue

            link_tag = item.attrs.get("href")
            if not link_tag:
                continue
            if not link_tag.startswith("http"):
                product_url = "https://www.radwell.com" + link_tag
            product_url = base_url + link_tag

            # Go to product page
            page = await browser.get(product_url)
            await asyncio.sleep(15)
            product_soup = await get_soup(page)

            return await parse_radwell_product_page(product_soup, scraped_sku, product_url)

        # No exact match found
        return [
            ProviderResult(
                supplier="Radwell",
                part_number=mpn,
                manufacturer="N/A",
                stock=0,
                price=0.0,
                url=search_url,
                exact_match=False
            )
        ]

    except Exception as e:
        print(f"[ERROR] Radwell exception for {mpn}: {e}")
        traceback.print_exc()
        return []

    finally:
        if own_browser:
            browser.stop()

async def parse_radwell_product_page(soup, mpn, url):
    buy_opts = soup.find_all("div", class_="option")

    if not buy_opts:
        return [
            ProviderResult(
                supplier="Radwell",
                part_number=mpn,
                manufacturer=None,
                stock=0,
                price=0.0,
                url=url,
                exact_match=True
            )
        ]

    new_option = None
    for opt in buy_opts:
        if opt.get("data-id") == "FNFP":  # NEW PRODUCT
            new_option = opt
            break

    if not new_option:
        # No new product option found
        return [
            ProviderResult(
                supplier="Radwell",
                part_number=mpn,
                manufacturer=None,
                stock=0,
                price=0.0,
                url=url,
                exact_match=True
            )
        ]

    # Stock
    stock_el = new_option.find("div", class_="option__stock__v2")
    stock_text = stock_el.get_text(strip=True) if stock_el else ""
    stock = 0 if "call" in stock_text.lower() else parse_int(stock_text)

    # Price
    price_el = new_option.find("span", class_="ActualPrice")
    price = parse_price(price_el.text if price_el else "")
    manufacturer = None
    # manufacturer-container
    brand_el = soup.find("div", class_="manufacturer-container")
    if brand_el:
        manufacturer = brand_el.get_text(strip=True)
    else:
        manufacturer = None

    return [
        ProviderResult(
            supplier="Radwell",
            part_number=mpn,
            manufacturer=manufacturer,
            stock=stock,
            price=price,
            url=url,
            exact_match=True
        )
    ]
