"""End-to-end: one raw input row -> one fully-assembled 252-column output row.

    classify (Dept/Class/Fine/Classpath)
      -> resolve brand (text match against the input description; if none is
         found, the row is honestly marked brand-unresolved rather than guessed)
      -> fetch the manufacturer's own site (real HTTP, not mocked)
      -> extract attributes with an evidence-verified LLM pass
      -> build the five description formats from whatever attributes were
         actually resolved (never invented)
      -> assemble the row, filling every field we have real grounds for and
         leaving the rest blank with a REVIEW flag and a stated reason

A source that is unreachable (bot-blocked, timed out, 403) does not stop the
row from being written -- Dept/Class/Fine/Classpath/Product Name/MPN passthrough
and brand-name styling (where resolvable) are still populated, and the
description fields degrade to whatever subset of attributes is actually known.
What changes is CONFIDENCE and the REVIEW flag, never silent fabrication.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from unihack import brand as brandmod
from unihack import describe
from unihack import search as searchmod
from unihack import source as sourcemod
from unihack import taxonomy
from unihack.extract import AttributeExtractor
from unihack.schema import attrs_for
from unihack.uom import normalize_unit


@dataclass
class InputRow:
    mfg_part_num: str
    part_desc: str
    e1_brand: str
    unilog_brand: str
    dib_brand: str
    part_manuf: str


@dataclass
class OutputRow:
    values: dict = field(default_factory=dict)
    confidence: float = 0.0
    decision: str = "REVIEW"
    reasons: list = field(default_factory=list)

    def set(self, col: str, val):
        if val not in (None, ""):
            self.values[col] = val


PLACEHOLDER_RE = None


def _is_placeholder(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith("--") and s.endswith("--")


def run_row(row: InputRow, extractor: AttributeExtractor | None = None,
           do_fetch: bool = True) -> OutputRow:
    out = OutputRow()
    extractor = extractor or AttributeExtractor()

    out.set("Mfg_Part_Num", row.mfg_part_num)
    out.set("Part_Desc", row.part_desc)
    out.set("MANUFACTURER_PART_NUMBER", row.mfg_part_num)
    if not _is_placeholder(row.e1_brand):
        out.set("E1_Brand", row.e1_brand)
    if not _is_placeholder(row.unilog_brand):
        out.set("Unilog_Brand", row.unilog_brand)
    if not _is_placeholder(row.dib_brand):
        out.set("DIB_Brand", row.dib_brand)
    if row.part_manuf and row.part_manuf != "-":
        out.set("Part_Manuf", row.part_manuf)

    cat = taxonomy.classify(row.part_desc)
    if not cat:
        out.reasons.append("could not classify into a known Major Appliances "
                           "sub-category from Part_Desc; left for manual triage")
        return out
    out.set("Dept", cat.dept)
    out.set("Class", cat.klass)
    out.set("Fine", cat.fine)
    out.set("Classpath", cat.classpath)
    out.set("Product Name", cat.product_name)
    if not cat.verified:
        out.reasons.append(f"Classpath for {cat.fine} is a best-effort extension "
                           f"of the verified Dishwasher pattern, not checked "
                           f"against the real LOV taxonomy -- unverified")

    bmatch = brandmod.resolve(row.part_desc)
    fetch_res = None
    if not bmatch.resolved and do_fetch:
        # No brand token anywhere in the description -- this is the real case
        # for both known ground-truth SKUs (PDSH4816AF, WDTS7024RZ), neither of
        # which names Frigidaire or Whirlpool in Part_Desc. A live search
        # resolves the manufacturer domain dynamically rather than leaving
        # every brandless row unresolved.
        found = searchmod.find_manufacturer_domain(row.mfg_part_num,
                                                    hint=cat.product_name.lower())
        if found:
            key, url = found
            style = brandmod.style_for(key)
            if style:
                bmatch = brandmod.BrandMatch(key, style, "(resolved via search)", True)
                out.reasons.append(f"brand resolved via live search on the model "
                                   f"number (no token in Part_Desc): found "
                                   f"{style.brand_display} at {url}")
    if bmatch.resolved:
        style = bmatch.style
        out.set("BRAND_NAME", style.brand_display)
        out.set("MANUFACTURER_NAME", style.manufacturer_name)
        if not style.verified:
            out.reasons.append(f"brand style/manufacturer for {bmatch.key} is a "
                               f"public-record default, not checked against the "
                               f"real 27,000-row manufacturer/brand master list")
        if do_fetch:
            fetch_res = sourcemod.fetch_first_reachable(bmatch.key, row.mfg_part_num)
            if fetch_res.ok:
                out.set("MFR URL", fetch_res.url)
            else:
                out.reasons.append(
                    f"manufacturer source unreachable ({fetch_res.reason}); "
                    f"attributes and marketing copy could not be sourced live")
    else:
        out.reasons.append(
            "brand could not be resolved from Part_Desc text or a live search "
            "on the model number; MANUFACTURER_NAME/BRAND_NAME left blank "
            "rather than guessed")

    specs = attrs_for(cat.key)
    resolved: dict[str, describe.ResolvedAttr] = {}
    n_verified_unit = 0
    if fetch_res and fetch_res.ok and specs:
        extracted = extractor.extract_all(fetch_res.text, row.mfg_part_num, specs)
        for label, ex in extracted.items():
            uom_norm, uom_known = ("", True)
            if ex.uom:
                uom_norm, uom_known = normalize_unit(ex.uom)
                if uom_known:
                    n_verified_unit += 1
            resolved[label] = describe.ResolvedAttr(label, ex.value, uom_norm)
            out.reasons.append(f"{label} sourced from {fetch_res.url} "
                               f"(quote verified byte-for-byte)")

    for i, spec in enumerate(specs, start=1):
        a = resolved.get(spec.label)
        if not a or not a.populated:
            continue
        out.set(f"ATTRIBUTE_LABEL {i}", spec.label)
        out.set(f"ATTRIBUTE_VALUE {i}", a.value)
        if a.uom:
            out.set(f"ATTRIBUTE_UOM {i}", a.uom)

    if bmatch.resolved and resolved:
        facts = describe.ProductFacts(
            brand_display=bmatch.style.brand_display,
            manufacturer_name=bmatch.style.manufacturer_name,
            mpn=row.mfg_part_num, product_name=cat.product_name,
            attrs=resolved)
        inv, inv_ok = describe.invoice_desc(facts)
        out.set("INVOICE_DESC", inv)
        out.set("MOBILE_DESC", describe.mobile_desc(facts))
        out.set("SHORT_DESC", describe.short_desc(facts))
        out.set("RETAIL_DESC", describe.retail_desc(facts))
        out.set("LONG_DESC1", describe.long_desc1(facts))
        if not inv_ok:
            out.reasons.append("INVOICE_DESC used a full-length token because "
                               "no verified abbreviation exists for it in our "
                               "small abbreviation table -- may exceed 40 chars")
    elif bmatch.resolved:
        out.set("SHORT_DESC", f"{bmatch.style.brand_display} {row.mfg_part_num} "
                              f"{cat.product_name}")
        out.reasons.append("no attributes were sourced (source unreachable), so "
                           "SHORT_DESC falls back to Brand + MPN + Product Name "
                           "only rather than a full formula")

    n_attrs = len(specs) or 1
    coverage = len(resolved) / n_attrs
    brand_ok = 1.0 if (bmatch.resolved and bmatch.style.verified) else (
        0.6 if bmatch.resolved else 0.0)
    source_ok = 1.0 if (fetch_res and fetch_res.ok) else 0.0
    out.confidence = round(0.4 * brand_ok + 0.3 * source_ok + 0.3 * coverage, 3)
    out.decision = "AUTO_PUBLISH" if out.confidence >= 0.75 else "REVIEW"
    return out
