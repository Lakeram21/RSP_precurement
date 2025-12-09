# run_scraper.py
import asyncio
from providers import scrape_digikey, scrape_galco, scrape_mouser, scrape_rs, scrape_ebay, scrape_radwell
import nodriver as uc
from bs4 import BeautifulSoup
import asyncio
import os
import platform
import shutil

def get_chrome_path():
    system = platform.system()
    if system == "Darwin":  # macOS
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    elif system == "Windows":
        possible_paths = [
            os.path.join(os.environ.get("PROGRAMFILES(X86)",""), "Google\\Chrome\\Application\\chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES",""), "Google\\Chrome\\Application\\chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA",""), "Google\\Chrome\\Application\\chrome.exe"),
        ]
        for path in possible_paths:
            if os.path.exists(path):
                return path
        # fallback to PATH
        return shutil.which("chrome.exe")
    else:  # Linux
        return shutil.which("google-chrome") or shutil.which("chromium-browser")

chrome_path = get_chrome_path()

async def get_or_create_browser(browser=None):
    """Return an existing browser or create a new one."""
    if browser:
        return browser, False  # existing browser, not owned
    new_browser = await uc.start(
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
#         "--window-size=1,1",
# "--window-position=-3000,-3000",  # 👈 off-screen (acts like minimized)
    ])
    return new_browser


async def main():
    new_browser = await get_or_create_browser()
    # mpn = "10923H19"
    mpn = "J1210HPL"
    results = []

    # results_1 = await scrape_galco(mpn, "ABB", new_browser)
    # results_2 = await scrape_digikey(mpn, new_browser)
    results_3 = await scrape_mouser(mpn, new_browser)
    # results_4 = await scrape_radwell(mpn, new_browser)
    # results_5 = await scrape_ebay(mpn, broswer=new_browser)
    # results.append(results_1)
    # results.append(results_2)
    results.append(results_3)
    # results.append(results_4)
    # results.append(results_5)

    for r in results:
        print(r)

asyncio.run(main())