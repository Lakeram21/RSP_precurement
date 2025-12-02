# run_scraper.py
import asyncio
from providers import scrape_digikey, scrape_galco, scrape_mouser, scrape_rs, scrape_ebay, scrape_radwell

async def main():
    # mpn = "10923H19"
    mpn = "ST202M-C5"
    results = await scrape_ebay(mpn)
    for r in results:
        print(r)

asyncio.run(main())