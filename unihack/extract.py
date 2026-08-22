"""Attribute extraction from a fetched manufacturer page, evidence-gated.

Reuses the Bedrock/Nova Lite Converse-API backend from specledger/llm.py
directly -- that plumbing (credentials, retries, response parsing) has nothing
appliance-specific about it. Only the prompt and the evidence contract differ.

The contract is the same one SpecLedger's whole architecture is built on: the
model returns a value and a VERBATIM quote; this module re-checks that quote
against the actual fetched text before accepting it. A quote that cannot be
found in the source is a fabrication and the candidate is dropped, regardless
of how plausible it looks -- "a fluent description made of invented values
scores zero," and the mechanism that prevents that is the same whether the
domain is electronic components or dishwashers.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from specledger.llm import make_backend, available            # noqa: E402
from unihack.schema import AttrSpec                            # noqa: E402

_WS = re.compile(r"\s+")


def _collapse(s: str) -> str:
    return _WS.sub(" ", s).strip()


def find_quote(text: str, quote: str) -> int:
    q = (quote or "").strip()
    if not q:
        return -1
    idx = text.find(q)
    if idx >= 0:
        return idx
    pat = r"\s+".join(re.escape(tok) for tok in _collapse(q).split(" "))
    m = re.search(pat, text, re.IGNORECASE)
    return m.start() if m else -1


SYSTEM = """You extract structured product attributes from a manufacturer's own
product/support page for one specific appliance model.

Rules you must never break:
1. Copy `quote` VERBATIM from the supplied page text. It is checked against the
   actual page character by character. A quote that does not appear there is
   discarded and counts as an error against you.
2. The page may describe MULTIPLE models or accessories. Only report a value
   that is stated for the EXACT model number given, not a sibling or related
   product.
3. If the page does not state this attribute for this exact model, return
   found=false. Abstaining is always acceptable and is never penalised.
   Guessing is.
"""

TOOL_NAME = "report_attribute"
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "value": {"type": "string", "description": "the value exactly as written, with its unit"},
        "quote": {"type": "string", "description": "VERBATIM text copied from the page"},
    },
    "required": ["found"],
}


class ExtractedAttr:
    def __init__(self, label: str, value: str, uom: str, quote: str,
                offset: int, verified: bool):
        self.label = label
        self.value = value
        self.uom = uom
        self.quote = quote
        self.offset = offset
        self.verified = verified


# Short categorical attributes (color, material, series name, ...) should be a
# handful of words, not a sentence. A verbatim-verified quote can still be
# semantically WRONG -- e.g. an LLM mislabeling a page's product-title text as
# the "Color" value, which passes verbatim verification (the string really is
# on the page) while being obviously not a color. This caught a real failure:
# the Whirlpool page's own title ("Eco Series Quiet Dishwasher with a washing
# 3rd Rack & Water Repellent Silverware Basket") was accepted as a Color value
# before this guard existed. Length and word-count are a blunt instrument, but
# a real color/material/series name is short; a mislabeled heading is not.
_SHORT_CATEGORICAL = {"Color", "Material", "Mounting Type", "Plug Type", "Load Type", "Fuel Type"}
_JUNK_WORDS = re.compile(r"\b(dishwasher|washer|dryer|refrigerator|series|rack|basket|"
                         r"cycle|feature|with a|and )\b", re.I)


def _plausible(label: str, value: str) -> bool:
    if label in _SHORT_CATEGORICAL:
        if len(value) > 40 or len(value.split()) > 5:
            return False
        if _JUNK_WORDS.search(value) and label not in ("Mounting Type",):
            return False
    return True


def _split_value_unit(raw: str, expected_unit: str | None) -> tuple[str, str]:
    raw = raw.strip()
    if expected_unit:
        m = re.match(rf"^([\-\d/. ]+)\s*{re.escape(expected_unit)}\b", raw, re.I)
        if m:
            return m.group(1).strip(), expected_unit
    return raw, ""


class AttributeExtractor:
    def __init__(self, backend=None):
        self.backend = backend or (make_backend() if available() else None)

    def extract_one(self, page_text: str, mpn: str, spec: AttrSpec) -> ExtractedAttr | None:
        if not self.backend:
            return None
        alias_hint = ", ".join(spec.aliases) if spec.aliases else spec.label
        prompt = (
            f"Model number: {mpn}\n"
            f"Attribute wanted: {spec.label} (also known as: {alias_hint})\n"
            f"Expected unit: {spec.unit or 'n/a'}\n\n"
            f"--- page text ---\n{page_text[:12000]}\n--- end page text ---"
        )
        try:
            resp = self.backend.call(
                prompt, 0.0, system=SYSTEM, tool_name=TOOL_NAME,
                tool_desc="Report one appliance attribute with the exact "
                          "evidence it came from.", tool_schema=TOOL_SCHEMA)
        except Exception:
            return None
        if not resp or not resp.get("found"):
            return None
        quote = (resp.get("quote") or "").strip()
        value = (resp.get("value") or "").strip()
        if not quote or not value:
            return None
        offset = find_quote(page_text, quote)
        verified = offset >= 0
        val, unit = _split_value_unit(value, spec.unit)
        if not _plausible(spec.label, val):
            return None
        return ExtractedAttr(spec.label, val, unit, quote, offset, verified)

    def extract_all(self, page_text: str, mpn: str, specs: list[AttrSpec]
                    ) -> dict[str, ExtractedAttr]:
        out = {}
        for spec in specs:
            got = self.extract_one(page_text, mpn, spec)
            if got and got.verified:
                out[spec.label] = got
        return out
