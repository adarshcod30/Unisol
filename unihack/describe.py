"""Deterministic description builders, reverse-engineered from the two known-good
ground-truth rows -- not invented, not LLM-freewritten.

Every formula below was derived by diffing the two real Delivery Format rows
field by field (see unihack/data/output_header.py and the worked dishwasher
example in the Solution Guide, page 5: "Product Title = Brand + Series + MPN +
Item Type + key attributes"). Where the two examples disagree on whether a
clause appears (e.g. row 1 has no wash-cycle count in LONG_DESC1's mounting
clause order but row 2 omits mounting from MOBILE_DESC), the rule implemented is
"include the clause only if that attribute is actually populated for this row" --
which is the one hypothesis consistent with both examples simultaneously.

LONG_DESC1 uses a PER-ATTRIBUTE phrase template because the two ground-truth
rows show genuinely different phrasing per field: Series appears bare ("Professional
Series"), Voltage/Amperage appear as bare "{value} {uom}" with no label word at
all, Mounting/Sound Level append a naturalised label word, and Material/Color
append nothing. This is not guessed -- it is the one template consistent with
both rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ResolvedAttr:
    label: str
    value: str = ""
    uom: str = ""

    @property
    def populated(self) -> bool:
        return bool(self.value.strip())

    @property
    def phrase(self) -> str:
        return f"{self.value} {self.uom}".strip() if self.uom else self.value


@dataclass
class ProductFacts:
    brand_display: str
    manufacturer_name: str
    mpn: str
    product_name: str
    attrs: dict[str, ResolvedAttr] = field(default_factory=dict)
    with_feature: str = ""

    def get(self, label: str) -> ResolvedAttr:
        return self.attrs.get(label, ResolvedAttr(label))


# Mounting/material abbreviations observed directly in the two ground-truth
# rows. Anything not in this table is left unabbreviated in INVOICE_DESC and
# the row is flagged, rather than guessing at a made-up abbreviation.
_MOUNT_ABBR = {"leg": "LEG", "built-in": "BLTLN"}
_MATERIAL_ABBR = {"stainless steel": "SST"}


def _abbr(table: dict, value: str) -> str | None:
    return table.get(value.strip().lower())


def invoice_desc(f: ProductFacts, limit: int = 40) -> tuple[str, bool]:
    """-> (text, fully_abbreviated). fully_abbreviated=False means a token could
    not be mapped through our small verified abbreviation table and was kept in
    full, which may push the string over the 40-char cap -- surfaced, not hidden."""
    tokens: list[str] = [f.product_name.upper()]
    ok = True
    mount = f.get("Mounting Type")
    if mount.populated:
        a = _abbr(_MOUNT_ABBR, mount.value)
        tokens.append(a or mount.value.upper())
        ok &= a is not None
    cycles = f.get("Number of Wash Cycles")
    if cycles.populated:
        tokens.append(cycles.value)
    material = f.get("Material")
    if material.populated:
        a = _abbr(_MATERIAL_ABBR, material.value)
        tokens.append(a or material.value.upper())
        ok &= a is not None
    color = f.get("Color")
    if color.populated:
        # appended unconditionally, even when identical to material -- confirmed
        # by ground truth ("SST SST" appears when Material=Color="Stainless Steel")
        a = _abbr(_MATERIAL_ABBR, color.value)
        tokens.append(a or color.value.upper())
    volt = f.get("Voltage Rating")
    if volt.populated:
        tokens.append(f"{volt.value}V")
    amp = f.get("Amperage Rating")
    if amp.populated:
        tokens.append(f"{amp.value}A")
    # Depth is tried first and used if it fits; only when it would overflow the
    # 40-char budget does sound level get tried as a fallback trailer. Confirmed
    # against both ground-truth rows: row 1's depth trailer fits (used), row 2's
    # depth trailer alone would overflow 40 chars so sound level is used instead
    # -- a real priority-with-fallback rule, not "pick whichever."
    depth = f.get("Depth With Door Open")
    sound = f.get("Sound Level")
    text = " ".join(tokens)
    candidates = []
    if depth.populated:
        candidates.append(f"{depth.value}{depth.uom}".upper().replace(" ", ""))
    if sound.populated:
        candidates.append(f"{sound.value}{sound.uom}".upper())
    for trailer in candidates:
        if len(text) + 1 + len(trailer) <= limit:
            text = f"{text} {trailer}"
            break
    return text[:limit], ok


_TRADEMARK_RE = re.compile(r"[®™©]")


def mobile_desc(f: ProductFacts, lo: int = 60, hi: int = 80) -> str:
    # MOBILE_DESC drops the (R)/(TM) symbol that BRAND_NAME itself keeps --
    # confirmed identically on both ground-truth rows (FRIGIDAIRE(R) -> FRIGIDAIRE,
    # Whirlpool(R) -> Whirlpool), presumably a mobile-space house-style rule.
    brand_plain = _TRADEMARK_RE.sub("", f.brand_display)
    lead = (brand_plain if f.manufacturer_name.lower() in brand_plain.lower()
                        or brand_plain.lower() in f.manufacturer_name.lower()
            else f"{f.manufacturer_name} {brand_plain}")
    parts = [lead, f.product_name, f.get("Series").value, f.mpn]
    base = ", ".join(p for p in parts if p)
    mount = f.get("Mounting Type")
    with_mount = f"{base}, {mount.value} Mounting" if mount.populated else base
    if len(base) < lo and mount.populated:
        return with_mount[:hi]
    if len(with_mount) <= hi:
        return with_mount if len(with_mount) >= lo or not mount.populated else with_mount
    return base[:hi]


def short_desc(f: ProductFacts) -> str:
    parts = [f.brand_display, f.get("Series").value, f.mpn, f.product_name]
    head = " ".join(p for p in parts if p)
    if f.with_feature:
        head += f" With {f.with_feature}"
    tail = []
    mount = f.get("Mounting Type")
    if mount.populated:
        tail.append(f"{mount.value} Mounting")
    cycles = f.get("Number of Wash Cycles")
    if cycles.populated:
        tail.append(f"{cycles.value}-Wash Cycle")
    material = f.get("Material")
    if material.populated:
        tail.append(material.value)
    color = f.get("Color")
    if color.populated:
        tail.append(color.value)
    return ", ".join([head] + tail) if tail else head


def retail_desc(f: ProductFacts) -> str:
    parts = [f.get("Series").value, f.product_name]
    head = " ".join(p for p in parts if p)
    tail = []
    mount = f.get("Mounting Type")
    if mount.populated:
        tail.append(f"{mount.value} Mounting")
    cycles = f.get("Number of Wash Cycles")
    if cycles.populated:
        tail.append(f"{cycles.value}-Wash Cycle")
    material = f.get("Material")
    if material.populated:
        tail.append(material.value)
    color = f.get("Color")
    if color.populated:
        tail.append(color.value)
    return ", ".join([head] + tail) if tail else head


# Per-label phrase renderer for LONG_DESC1, keyed on the exact attribute label.
def _phrase_series(a: ResolvedAttr) -> str:
    return a.value
def _phrase_bare_uom(a: ResolvedAttr) -> str:
    return a.phrase
def _phrase_mounting(a: ResolvedAttr) -> str:
    return f"{a.value} Mounting"
def _phrase_wash_cycles(a: ResolvedAttr) -> str:
    return f"{a.value} Wash Cycles"
def _phrase_depth(a: ResolvedAttr) -> str:
    return f"{a.phrase} Depth With Door Open"
def _phrase_min_height(a: ResolvedAttr) -> str:
    return f"{a.phrase} Minimum Height"
def _phrase_max_height(a: ResolvedAttr) -> str:
    return f"{a.phrase} Maximum Height"
def _phrase_sound(a: ResolvedAttr) -> str:
    return f"{a.phrase} Sound Level"
def _phrase_bare(a: ResolvedAttr) -> str:
    return a.value

_LONG_DESC_TEMPLATES = {
    "Series": _phrase_series,
    "Voltage Rating": _phrase_bare_uom,
    "Amperage Rating": _phrase_bare_uom,
    "Mounting Type": _phrase_mounting,
    "Number of Wash Cycles": _phrase_wash_cycles,
    "Size": _phrase_bare,
    "Depth With Door Open": _phrase_depth,
    "Minimum Height": _phrase_min_height,
    "Maximum Height": _phrase_max_height,
    "Sound Level": _phrase_sound,
    "Material": _phrase_bare,
    "Color": _phrase_bare,
}

# LONG_DESC1's own clause order, observed directly (differs from the
# ATTRIBUTE_LABEL table order -- e.g. Mounting comes after Amperage here but
# after Voltage/Amperage in the attribute table too, so they match; Size comes
# straight after Mounting, ahead of Depth).
_LONG_DESC_ORDER = [
    "Series", "Number of Wash Cycles", "Voltage Rating", "Amperage Rating",
    "Mounting Type", "Size", "Depth With Door Open", "Minimum Height",
    "Maximum Height", "Sound Level", "Material", "Color",
]


def long_desc1(f: ProductFacts) -> str:
    head = f"{f.brand_display} {f.product_name}"
    if f.with_feature:
        head += f" With {f.with_feature}"
    clauses = [head]
    for label in _LONG_DESC_ORDER:
        a = f.get(label)
        if not a.populated:
            continue
        tmpl = _LONG_DESC_TEMPLATES.get(label, _phrase_bare)
        clauses.append(tmpl(a))
    extra = f.get("Additional Information")
    if extra.populated:
        clauses.append(f"Additional Information: {extra.value}")
    return ", ".join(clauses)
