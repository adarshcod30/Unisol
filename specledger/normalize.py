"""Unit and enum normalization.

Industrial catalogs die of unit drift: 100mA and 0.1A are the same spec written two
ways, and a catalog that stores the strings can never dedupe, filter, or compare
them. Everything lands in the schema's canonical unit before it is stored.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import pint

from .schema import AttributeSpec

ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=False)

# Datasheet glyphs -> pint-parseable unit names
UNIT_ALIASES = {
    "°c": "degC", "degc": "degC", "℃": "degC", "c": "degC",
    "°f": "degF", "degf": "degF",
    "μa": "microampere", "µa": "microampere", "ua": "microampere",
    "μv": "microvolt", "µv": "microvolt", "uv": "microvolt",
    "μf": "microfarad", "µf": "microfarad", "uf": "microfarad",
    "μs": "microsecond", "µs": "microsecond", "us": "microsecond",
    "ω": "ohm", "Ω": "ohm", "kω": "kiloohm", "mω": "megaohm",
    "ma": "milliampere", "ka": "kiloampere", "a": "ampere",
    "mv": "millivolt", "kv": "kilovolt", "v": "volt",
    "mw": "milliwatt", "kw": "kilowatt", "w": "watt",
    "pf": "picofarad", "nf": "nanofarad",
    "ns": "nanosecond", "ms": "millisecond", "s": "second",
    "mm": "millimeter", "cm": "centimeter", "in": "inch", '"': "inch",
    "db": "decibel",
}

NUMBER_RE = re.compile(r"[-+−]?\d+(?:[.,]\d+)?")
# A unit token as it appears glued to or beside a number in a datasheet.
UNIT_TOKEN_RE = re.compile(
    r"(?:°\s?[CF]|[munpkKM]?(?:A|V|W|F|s|Hz|Ω|ohm)|dB|%|degC|degF|℃)\b",
    re.IGNORECASE,
)

TRUEISH = {"yes", "true", "compliant", "rohs-compliant", "rohs compliant", "lead free",
           "lead-free", "pb-free", "pb free", "halogen free", "y", "adjustable"}
FALSEISH = {"no", "false", "non-compliant", "not compliant", "n", "fixed"}


def _clean_unit(u: str) -> str:
    u = (u or "").strip().replace(" ", "").replace(" ", "")
    key = u.lower()
    if key in UNIT_ALIASES:
        return UNIT_ALIASES[key]
    key2 = key.replace("°", "deg")
    return UNIT_ALIASES.get(key2, u)


def parse_number(raw: str) -> Optional[float]:
    m = NUMBER_RE.search((raw or "").replace("−", "-"))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def compatible(unit: str, target: str) -> bool:
    """Is `unit` dimensionally convertible to the schema's canonical unit?"""
    try:
        u, t = _clean_unit(unit), _clean_unit(target)
        return ureg.Unit(u).dimensionality == ureg.Unit(t).dimensionality
    except Exception:
        return False


def convert(magnitude: float, unit: str, target: str) -> Optional[float]:
    try:
        u, t = _clean_unit(unit), _clean_unit(target)
        if u == t:
            return float(magnitude)
        q = ureg.Quantity(magnitude, u)
        return float(q.to(t).magnitude)
    except Exception:
        return None


def canon_enum(raw: str, values: tuple) -> Optional[str]:
    s = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower()).strip("_")
    for v in values:
        if s == v.lower():
            return v
    for v in values:
        if v.lower() in s or s in v.lower():
            return v
    return None


def to_bool(raw: str) -> Optional[bool]:
    s = (raw or "").strip().lower()
    if s in TRUEISH:
        return True
    if s in FALSEISH:
        return False
    if any(t in s for t in TRUEISH):
        return True
    if any(t in s for t in FALSEISH):
        return False
    return None


def normalize_value(raw: str, spec: AttributeSpec, unit_hint: str = "") -> tuple[Any, Optional[str], str, str]:
    """-> (value, unit, display, error)"""
    raw = (raw or "").strip()
    if not raw:
        return None, None, "", "empty"

    if spec.dtype == "number":
        mag = parse_number(raw)
        if mag is None:
            return None, None, "", f"no number in {raw!r}"
        unit = unit_hint or ""
        if not unit:
            m = UNIT_TOKEN_RE.search(raw[NUMBER_RE.search(raw.replace("−", "-")).end():]
                                     if NUMBER_RE.search(raw.replace("−", "-")) else raw)
            unit = m.group(0) if m else (spec.unit or "")
        if spec.unit and not compatible(unit, spec.unit):
            return None, None, "", f"unit {unit!r} not compatible with {spec.unit!r}"
        val = convert(mag, unit, spec.unit) if spec.unit else mag
        if val is None:
            return None, None, "", f"cannot convert {mag} {unit} -> {spec.unit}"
        val = round(val, 6)
        disp = f"{val:g} {spec.unit}" if spec.unit else f"{val:g}"
        return val, spec.unit, disp, ""

    if spec.dtype == "enum":
        v = canon_enum(raw, spec.enum)
        if v is None:
            return None, None, "", f"{raw!r} not in {spec.enum}"
        return v, None, v.replace("_", " ").title(), ""

    if spec.dtype == "bool":
        v = to_bool(raw)
        if v is None:
            return None, None, "", f"{raw!r} not boolean"
        return v, None, ("Yes" if v else "No"), ""

    s = re.sub(r"\s+", " ", raw).strip(" .,;:")
    if not s:
        return None, None, "", "empty after cleanup"
    return s, None, s, ""


def normalize_candidate(cand, spec: AttributeSpec, unit_hint: str = ""):
    v, u, disp, err = normalize_value(cand.raw_value, spec, unit_hint or (cand.unit or ""))
    cand.value, cand.unit, cand.display, cand.normalize_error = v, u, disp, err
    return cand
