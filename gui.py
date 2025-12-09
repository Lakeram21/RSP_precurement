import flet as ft
import asyncio
from models import ProviderResult
from providers import (
    scrape_digikey,
    scrape_mouser,
    scrape_rs,
    scrape_galco,
    scrape_ebay,
    scrape_radwell,
)

import nodriver as uc
import os
import platform
import shutil


# ======================================================
#   GLOBAL BROWSER INSTANCE
# ======================================================
GLOBAL_BROWSER = None


def get_chrome_path():
    system = platform.system()
    if system == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    elif system == "Windows":
        possible_paths = [
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google\\Chrome\\Application\\chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Google\\Chrome\\Application\\chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google\\Chrome\\Application\\chrome.exe"),
        ]
        for path in possible_paths:
            if os.path.exists(path):
                return path
        return shutil.which("chrome.exe")
    else:
        return shutil.which("google-chrome") or shutil.which("chromium-browser")


chrome_path = get_chrome_path()


# ======================================================
#   ALWAYS RETURN SAME BROWSER INSTANCE
# ======================================================
async def get_or_create_browser():
    global GLOBAL_BROWSER

    if GLOBAL_BROWSER:
        return GLOBAL_BROWSER

    GLOBAL_BROWSER = await uc.start(
        headless=False,
        no_sandbox=True,
        executable_path=chrome_path,
        user_data_dir="/tmp/chrome_profile",
        browser_args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-blink-features=AutomationControlled",
        ],
    )

    return GLOBAL_BROWSER


# ======================================================
#   INSERT ROW FUNCTION WITH RESCRAPE
# ======================================================
def insert_row_fn(table, result_list, header, manufacturer, mpn, page):
    if result_list:
        table.rows.append(
            ft.DataRow(
                cells=[ft.DataCell(ft.Text(header, weight="bold"))]
                + [ft.DataCell(ft.Text(""))] * 8
            )
        )

    for r in result_list:

        def make_rescrape_button(r):
            async def perform_rescrape(e):
                provider_map = {
                    "Digi-Key": scrape_digikey,
                    "Mouser": scrape_mouser,
                    "RS Online": scrape_rs,
                    "Galco": scrape_galco,
                    "eBay": scrape_ebay,
                    "Radwell": scrape_radwell,
                }

                browser = await get_or_create_browser()
                scraper = provider_map[r["__provider"]]

                if r["__provider"] == "Galco":
                    new_res = await scraper(mpn, manufacturer, browser=browser)
                else:
                    new_res = await scraper(mpn, browser=browser)

                if new_res:
                    new_res = (
                        new_res[0].dict()
                        if isinstance(new_res[0], ProviderResult)
                        else new_res[0]
                    )

                    # Update row
                    e.control.parent.cells[3].content.value = str(new_res.get("stock", ""))
                    e.control.parent.cells[4].content.value = str(new_res.get("price", ""))
                    e.control.parent.cells[5].content.url = new_res.get("url", "")
                    e.control.parent.cells[6].content.value = "Yes" if new_res.get("exact_match") else "No"

                page.update()

            return ft.ElevatedButton("🔄 Rescrape", on_click=perform_rescrape)

        table.rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(r.get("supplier", ""))),
                    ft.DataCell(ft.Text(r.get("part_number", ""))),
                    ft.DataCell(ft.Text(r.get("manufacturer", manufacturer))),
                    ft.DataCell(ft.Text(str(r.get("stock", "")))),
                    ft.DataCell(ft.Text(str(r.get("price", "")))),
                    ft.DataCell(ft.TextButton("Open", url=r.get("url", ""))),
                    ft.DataCell(ft.Text("Yes" if r.get("exact_match") else "No")),
                    ft.DataCell(ft.Text(r.get("scraped_sku", ""))),
                    ft.DataCell(make_rescrape_button(r)),
                ]
            )
        )


# ======================================================
#   RUN SCRAPERS USING ONE SHARED BROWSER
# ======================================================
async def run_scrapers(mpn, manufacturer, page, table, status_text, enabled_providers):
    status_text.value = f"🔍 Searching for '{mpn}' by '{manufacturer}'..."
    page.update()

    table.rows.clear()
    all_results = []

    # Get shared browser
    browser = await get_or_create_browser()

    scrapers = {
        "Digi-Key": (scrape_digikey, False),
        "Mouser": (scrape_mouser, False),
        "RS Online": (scrape_rs, False),
        "Galco": (scrape_galco, True),
        "eBay": (scrape_ebay, False),
        "Radwell": (scrape_radwell, False),
    }

    for name in enabled_providers:
        scraper, needs_brand = scrapers[name]

        status_text.value = f"➡️ Scraping {name}..."
        page.update()

        try:
            if needs_brand:
                results = await scraper(mpn, manufacturer, browser=browser)
            else:
                results = await scraper(mpn, browser=browser)
                print(f"[INFO] Scraped {results} results from {name} for {mpn}")

            if results:
                for r in results:
                    d = r.dict() if isinstance(r, ProviderResult) else r
                    d["__provider"] = name
                    all_results.append(d)

        except Exception as e:
            status_text.value = f"❌ Error scraping {name}: {e}"
            page.update()

    # Organize results
    exact = [r for r in all_results if r.get("exact_match")]
    non_exact = [r for r in all_results if not r.get("exact_match")]

    insert_row_fn(table, exact, "EXACT MATCHES", manufacturer, mpn, page)
    insert_row_fn(table, non_exact, "ALTERNATIVES / NOT EXACT", manufacturer, mpn, page)

    status_text.value = "✅ Done!"
    page.update()
    # browser.stop()


# ======================================================
#   UI
# ======================================================
def main(page: ft.Page):
    page.title = "RSP Supply Procurement Scraper"
    page.window_width = 1200
    page.window_height = 700
    page.theme_mode = ft.ThemeMode.LIGHT

    manufacturer_input = ft.TextField(label="Manufacturer", width=250)
    mpn_input = ft.TextField(label="Part Number (MPN)", width=250)
    status_text = ft.Text("Ready", color=ft.Colors.GREY)

    results_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Supplier")),
            ft.DataColumn(ft.Text("Part #")),
            ft.DataColumn(ft.Text("Manufacturer")),
            ft.DataColumn(ft.Text("Stock")),
            ft.DataColumn(ft.Text("Price")),
            ft.DataColumn(ft.Text("URL")),
            ft.DataColumn(ft.Text("Exact Match")),
            ft.DataColumn(ft.Text("Scraped SKU")),
            ft.DataColumn(ft.Text("Action")),
        ],
        rows=[]
    )

    scrollable_results = ft.ListView(
        controls=[results_table],
        spacing=10,
        expand=True,
        auto_scroll=False,
    )

    select_all = ft.Checkbox(label="Select All", value=True)
    provider_checks = {
        "Digi-Key": ft.Checkbox(label="Digi-Key", value=True),
        "Mouser": ft.Checkbox(label="Mouser", value=False),
        "RS Online": ft.Checkbox(label="RS Online", value=True),
        "Galco": ft.Checkbox(label="Galco", value=True),
        "eBay": ft.Checkbox(label="eBay", value=True),
        "Radwell": ft.Checkbox(label="Radwell", value=True),
    }

    def toggle_all(e):
        for cb in provider_checks.values():
            cb.value = select_all.value
        page.update()

    select_all.on_change = toggle_all

    async def handle_search(e):
        mpn = mpn_input.value.strip()
        manu = manufacturer_input.value.strip()

        if not mpn:
            status_text.value = "⚠️ Please enter an MPN."
            page.update()
            return

        enabled = [name for name, cb in provider_checks.items() if cb.value]
        await run_scrapers(mpn, manu, page, results_table, status_text, enabled)

    search_btn = ft.ElevatedButton("Search", on_click=handle_search)

    page.add(
        ft.Row([manufacturer_input, mpn_input]),
        ft.Row([select_all] + list(provider_checks.values())),
        search_btn,
        status_text,
        ft.Container(content=scrollable_results, expand=True),
    )


ft.app(target=main)
