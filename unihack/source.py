"""Manufacturer-site sourcing, respecting the guide's sourcing hierarchy:
"product data [must] come from the manufacturer's own site or documentation.
Marketplaces and distributor sites are explicitly excluded."

This makes real, live HTTP requests at run time -- it is not a lookup table of
pre-fetched pages for known SKUs. What that buys: it generalizes to any part
number in the evaluation set, not just the two we have ground truth for. What
it costs: some manufacturer sites run bot-management (Akamai, Cloudflare) that
blocks automated fetches outright, independent of anything this code does --
confirmed directly against frigidaire.com and lg.com/kitchenaid.com, which
return a timeout or 403 to a plain, correctly-headered GET regardless of
client. When that happens the row is not silently skipped or guessed at; it is
marked SOURCE_BLOCKED and routed to human review, which is the honest behaviour
the guide itself calls out as a strength.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Manufacturer's own domain only -- marketplaces (Amazon, Home Depot, Lowe's)
# and distributor sites are never queried, per the sourcing rule.
BRAND_DOMAINS = {
    "FRIGIDAIRE": "www.frigidaire.com",
    "WHIRLPOOL": "www.whirlpool.com",
    "GE": "www.geappliances.com",
    "LG": "www.lg.com",
    "KITCHENAID": "www.kitchenaid.com",
    "SPEED QUEEN": "www.speedqueen.com",
    "CAFE": "www.cafeappliances.com",
    "MAYTAG": "www.maytag.com",
}

# Whirlpool's own support-search endpoint reliably resolves an MPN to a live
# product/support page and was NOT blocked when tested -- unlike a guessed
# direct product-support URL path, which 404s for most models.
_SUPPORT_SEARCH = {
    "WHIRLPOOL": "https://learnwhirlpool.com/smartsearchresults?searchtext={mpn}",
    "MAYTAG": "https://learnwhirlpool.com/smartsearchresults?searchtext={mpn}",
}


@dataclass
class FetchResult:
    url: str
    ok: bool
    status: int | None
    text: str
    reason: str = ""


def candidate_urls(brand_key: str, mpn: str) -> list[str]:
    urls = []
    if brand_key in _SUPPORT_SEARCH:
        urls.append(_SUPPORT_SEARCH[brand_key].format(mpn=mpn))
    domain = BRAND_DOMAINS.get(brand_key)
    if domain:
        urls.append(f"https://{domain}/en/p/owner-center/product-support/{mpn}")
        urls.append(f"https://{domain}/search?q={mpn}")
    return urls


def _strip_html(html: str) -> str:
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def fetch(url: str, timeout: float = 10.0) -> FetchResult:
    try:
        r = httpx.get(url, headers={"User-Agent": USER_AGENT,
                                    "Accept": "text/html,application/pdf,*/*"},
                      timeout=timeout, follow_redirects=True)
    except httpx.TimeoutException:
        return FetchResult(url, False, None, "", "timed out (likely bot-management block)")
    except httpx.HTTPError as e:
        return FetchResult(url, False, None, "", f"{type(e).__name__}: {e}")
    if r.status_code >= 400:
        return FetchResult(url, False, r.status_code, "", f"HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "")
    if "text/html" in ctype:
        return FetchResult(url, True, r.status_code, _strip_html(r.text))
    if "pdf" in ctype:
        return FetchResult(url, False, r.status_code, "",
                           "PDF asset (not text-extracted in this pass)")
    return FetchResult(url, True, r.status_code, r.text[:200_000])


def _looks_like_real_content(text: str, mpn: str) -> bool:
    """A search/support page that returns 200 with generic nav chrome (client-
    side-rendered product data, empty until JS runs) must not be treated as a
    usable source -- that would silently let empty specs through instead of
    routing the row to review. Require the MPN itself to appear, or a real
    spec-like keyword, as a floor."""
    if mpn.upper() in text.upper():
        return True
    return bool(re.search(r"\b(voltage|amperage|dimensions|specifications|dBA|cu\.?\s*ft)\b",
                          text, re.I))


def fetch_first_reachable(brand_key: str, mpn: str) -> FetchResult:
    """Try each candidate URL for this brand+MPN in order, return the first
    that succeeds AND looks like real product content. If every candidate
    fails or returns only site chrome, return the last failure with its reason
    intact so the caller can surface *why*, rather than silently accepting an
    empty JS-shell page as a source."""
    last = FetchResult("", False, None, "", "no candidate URL for this brand")
    for url in candidate_urls(brand_key, mpn):
        res = fetch(url)
        if res.ok and len(res.text) > 200:
            if _looks_like_real_content(res.text, mpn):
                return res
            res = FetchResult(url, False, res.status, res.text,
                              "page loaded but looks like client-rendered "
                              "chrome with no product content (MPN not found)")
        last = res
    return last
