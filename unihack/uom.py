"""Unit-of-measure normalization.

We do not have Unilog's real 500-entry UOM standards file, so this is a small,
explicitly-scoped table covering only the units actually observed in the two
known-good ground-truth rows (V, A, in, dBA, kW-hr, hr) plus the handful of
adjacent units a major-appliance spec sheet commonly states (W, cu ft, lb).
It is not a general-purpose UOM engine and does not claim to be Unilog's
approved list -- a unit outside this table is left as-is and flagged for
review rather than silently guessed at.

The one house-style rule we DO have directly from the guide text is applied
unconditionally: always a space between the number and the unit ("24 in", not
"24in") -- except inside INVOICE_DESC, where the ground truth itself
("50-1/4IN") shows the space is dropped to fit the 40-character budget.
"""
from __future__ import annotations

_KNOWN = {
    "v": "V", "volt": "V", "volts": "V",
    "a": "A", "amp": "A", "amps": "A", "ampere": "A", "amperes": "A",
    "in": "in", "inch": "in", "inches": "in", '"': "in",
    "dba": "dBA", "db": "dBA",
    "kwh": "kW-hr", "kw-hr": "kW-hr", "kwhr": "kW-hr",
    "hr": "hr", "hrs": "hr", "hour": "hr", "hours": "hr",
    "w": "W", "watt": "W", "watts": "W",
    "cuft": "cu ft", "cu ft": "cu ft", "cu. ft.": "cu ft",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
}


def normalize_unit(raw: str) -> tuple[str, bool]:
    """-> (normalized_unit_or_original, known). known=False means this unit is
    outside our small verified table and the caller should lower confidence."""
    key = raw.strip().lower().rstrip(".")
    if key in _KNOWN:
        return _KNOWN[key], True
    return raw.strip(), False


def with_space(value: str, unit: str) -> str:
    return f"{value} {unit}"
