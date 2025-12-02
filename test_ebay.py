import base64
import requests

# ------------------------------------------------------
# INSERT **NEW** CLIENT ID AND SECRET AFTER ROTATING THEM
# ------------------------------------------------------

CLIENT_ID = ""
CLIENT_SECRET = ""

import re


# ------------------------------------------------------
# Step 1 — Generate OAuth Token
# ------------------------------------------------------
def get_access_token():
    creds = f"{CLIENT_ID}:{CLIENT_SECRET}"
    encoded = base64.b64encode(creds.encode()).decode()

    url = "https://api.ebay.com/identity/v1/oauth2/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded}"
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }

    resp = requests.post(url, headers=headers, data=data)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ------------------------------------------------------
# SKU Token Extraction
# ------------------------------------------------------
def extract_sku_tokens(title):
    """
    Extracts SKU-like alphanumeric tokens.
    Example input: "Hoffman CSD12126B Wall Mount"
    Output: ["Hoffman", "CSD12126B", "Wall", "Mount"]
    """
    return re.findall(r"[A-Za-z0-9\-]{4,}", title)


# ------------------------------------------------------
# Step 2 — Search eBay with All Filters + Exact/Alt Matching
# ------------------------------------------------------
def search_exact_and_alternatives(access_token, sku, limit=30):
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    params = {
        "q": f'"{sku}"',   # exact quoted search
        "limit": limit,
        "filter": "conditionIds:{1000},itemLocationCountry:US"
    }

    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("itemSummaries", [])
    print(f"\nRaw results: {len(items)}\n")

    results_exact = []
    results_alternative = []

    for item in items:
        title = item.get("title", "")
        title_upper = title.upper()
        sku_upper = sku.upper()

        # --- Extract SKU-like tokens from title ---
        tokens = extract_sku_tokens(title_upper)

        # --- EXACT match detection ---
        exact_match = any(t == sku_upper for t in tokens)

        # --- ALTERNATIVE matches ---
        alt_candidates = [t for t in tokens if t != sku_upper]

        # --- Must have photos ---
        if "image" not in item or not item["image"].get("imageUrl"):
            continue

        # --- Seller feedback ≥98% ---
        seller = item.get("seller", {})
        feedback = float(seller.get("feedbackPercentage", 0))
        if feedback < 98.0:
            continue

        # --- Return policy filter (API often missing this) ---
        return_terms = item.get("returnTerms", {})
        returns_accepted = return_terms.get("returnsAccepted")

        # Reject only if explicitly "no returns"
        if returns_accepted is False:
            continue

        # --- Classify ---
        if exact_match:
            item["matchType"] = "exact"
            results_exact.append(item)
        else:
            item["matchType"] = "alternative"
            item["skuCandidates"] = alt_candidates
            results_alternative.append(item)

    # --------------------------------------------------
    # PRINT RESULTS
    # --------------------------------------------------
    print("=== EXACT MATCHES ===\n")
    for item in results_exact:
        title = item.get("title")
        price = item.get("price", {}).get("value")
        currency = item.get("price", {}).get("currency")
        url = item.get("itemWebUrl")
        feedback = item.get("seller", {}).get("feedbackPercentage")
        print(f"Title: {title}")
        print(f"Price: {price} {currency}")
        print(f"Feedback: {feedback}%")
        print(f"URL: {url}\n")

    print("\n=== ALTERNATIVE MATCHES (not exact) ===\n")
    for item in results_alternative:
        title = item.get("title")
        price = item.get("price", {}).get("value")
        currency = item.get("price", {}).get("currency")
        url = item.get("itemWebUrl")
        feedback = item.get("seller", {}).get("feedbackPercentage")
        skus = ", ".join(item.get("skuCandidates", []))
        print(f"Title: {title}")
        print(f"Price: {price} {currency}")
        print(f"Feedback: {feedback}%")
        print(f"URL: {url}")
        print(f"SKU candidates: {skus}\n")


# ------------------------------------------------------
# MAIN PROGRAM
# ------------------------------------------------------
if __name__ == "__main__":
    sku = "CSD12126"

    print("Generating OAuth token...")
    token = get_access_token()
    print("Token received.\n")

    search_exact_and_alternatives(token, sku)
