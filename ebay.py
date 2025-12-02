# ────────────────────────────────
# eBay API Scraper (FULL ASYNC, FINAL VERSION)
# ────────────────────────────────

import aiohttp
import base64
import re
import time
from typing import List, Optional
from models import ProviderResult

EBAY_CLIENT_ID = "YOUR_NEW_CLIENT_ID"
EBAY_CLIENT_SECRET = "YOUR_NEW_CLIENT_SECRET"

# Cache the token to avoid requesting a new one on every call
_cached_token: Optional[str] = None
_cached_token_expire: float = 0


# ------------------------------------------------------
# Generate or reuse OAuth token
# ------------------------------------------------------
async def ebay_get_access_token():
    global _cached_token, _cached_token_expire

    # Reuse token if not expired
    if _cached_token and time.time() < _cached_token_expire:
        return _cached_token

    creds = f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}"
    encoded = base64.b64encode(creds.encode()).decode()

    url = "https://api.ebay.com/identity/v1/oauth2/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded}",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data) as resp:
            resp.raise_for_status()
            payload = await resp.json()

            # Token caching (expires in ~7200 seconds)
            _cached_token = payload["access_token"]
            _cached_token_expire = time.time() + payload.get("expires_in", 3600)

            return _cached_token


# ------------------------------------------------------
# Token extractor
# ------------------------------------------------------
def extract_sku_tokens(title: str):
    """Extract SKU-like alphanumeric tokens from a title."""
    return re.findall(r"[A-Za-z0-9\-]{4,}", title)


# ------------------------------------------------------
# FULL ASYNC eBay Scraper
# ------------------------------------------------------
async def scrape_ebay(mpn: str) -> List[ProviderResult]:
    print(f"🔍 Searching eBay for: {mpn}")

    results: List[ProviderResult] = []

    try:
        token = await ebay_get_access_token()
    except Exception as e:
        print("❌ eBay OAuth error:", e)
        return results

    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    params = {
        "q": f'"{mpn}"',  # exact quoted match
        "limit": "50",
        "filter": "conditionIds:{1000},itemLocationCountry:US"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

    except Exception as e:
        print("❌ eBay API error:", e)
        return results

    items = data.get("itemSummaries", [])
    print(f"📦 eBay returned {len(items)} raw listings")

    sku_upper = mpn.upper()

    for item in items:
        title = item.get("title", "")
        if not title:
            continue

        title_upper = title.upper()

        # Extract SKU-like tokens
        tokens = extract_sku_tokens(title_upper)

        # Detect exact match
        exact_match = any(t == sku_upper for t in tokens)

        # Alternative SKU candidates
        alt_candidates = [t for t in tokens if t != sku_upper]

        # Must have photos
        if not item.get("image", {}).get("imageUrl"):
            continue

        # Seller feedback filter
        seller = item.get("seller", {})
        feedback_pct = float(seller.get("feedbackPercentage", 0))
        if feedback_pct < 98.0:
            continue

        # Returns-accepted filter
        returns_flag = item.get("returnTerms", {}).get("returnsAccepted")
        if returns_flag is False:
            continue  # reject explicit "no returns"

        # Price
        price = float(item.get("price", {}).get("value", 0.0))

        # URL
        url = item.get("itemWebUrl", "")

        # Final structured result
        results.append(
            ProviderResult(
                supplier="eBay",
                part_number=mpn,
                manufacturer=None,
                stock=0,
                price=price,
                url=url,
                exact_match=exact_match
            )
        )

    print(f"✅ eBay scrape complete for {mpn}: {len(results)} results")
    return results
