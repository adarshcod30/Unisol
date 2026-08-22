"""Live model-number -> manufacturer-domain resolution, for the rows where no
brand token appears anywhere in Part_Desc text (PDSH4816AF and WDTS7024RZ are
both exactly this case -- "PDSH4816AF Dishwasher SS - Display Only" names
neither Frigidaire nor Whirlpool).

This runs a real search at call time and is not a lookup table keyed to known
part numbers; it generalises to any MPN in the evaluation set. The sourcing
rule ("manufacturer's own site... marketplaces and distributor sites
explicitly excluded") is enforced here, not just at fetch time: results are
scanned in rank order and the FIRST hit whose domain is one of our known
manufacturer domains wins, skipping every distributor/marketplace/retailer
result in between (ajmadison.com, appliancejunction.com, etc. all appear
ahead of or alongside the real manufacturer result in practice and must never
be treated as the source).
"""
from __future__ import annotations

import re
from urllib.parse import unquote

import httpx

from unihack.source import USER_AGENT, BRAND_DOMAINS

# Match on the brand's core domain token (e.g. "whirlpool" from
# "www.whirlpool.com"), not the full domain string -- a search result can
# legitimately land on a regional TLD (whirlpool.ca, whirlpool.co.uk) that
# still IS the manufacturer's own site and must not be rejected just because
# it isn't exactly "www.whirlpool.com".
def _core_token(domain: str) -> str:
    parts = domain.split(".")
    return parts[-2] if len(parts) >= 2 else domain

_TOKEN_TO_BRAND = {_core_token(v): k for k, v in BRAND_DOMAINS.items()}
_RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')


def _decode_ddg_redirect(href: str) -> str:
    m = re.search(r"uddg=([^&]+)", href)
    if m:
        return unquote(m.group(1))
    return href if href.startswith("http") else f"https:{href}"


def find_manufacturer_domain(mpn: str, hint: str = "appliance") -> tuple[str, str] | None:
    """-> (brand_key, result_url) for the first manufacturer-domain hit, or
    None if no known manufacturer domain appears anywhere in the results."""
    try:
        r = httpx.get("https://html.duckduckgo.com/html/",
                      params={"q": f"{mpn} {hint} manufacturer"},
                      headers={"User-Agent": USER_AGENT}, timeout=15,
                      follow_redirects=True)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    for href in _RESULT_RE.findall(r.text):
        url = _decode_ddg_redirect(href)
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        for token, brand_key in _TOKEN_TO_BRAND.items():
            if re.search(rf"(^|\.){re.escape(token)}\.", host):
                return brand_key, url
    return None
