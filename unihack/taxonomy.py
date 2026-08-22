"""Category classification: Dept / Class / Fine / Classpath / Product Name.

Ground truth: both known-good rows are Dishwashers, classified

    Dept="Appliances", Class="Large Appliances", Fine="Dishwashers"
    Classpath="Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers"

Note the ground truth itself is imperfect here -- PDSH4816AF is leg-mounted, not
built-in, yet both dishwasher rows share the same "Built-In Dishwashers" leaf.
Rather than "fix" this by inventing a mounting-aware leaf we have no authority to
invent, we match the observed ground truth exactly for Dishwashers and extend the
same Classpath *shape* to sibling appliance types by pattern, flagging those as
UNVERIFIED since we have no ground truth for them and no access to the real
~161,000-row LOV that would confirm the exact leaf wording.

This is a rule-based classifier scoped to the Major Appliances categories present
in the 1000-item sample (dishwasher, washer, dryer, laundry center, range,
cooktop, microwave, refrigerator, freezer). It is not a general-purpose taxonomy
engine -- extending it to other departments means adding another rule block, not
rewriting the module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Category:
    key: str
    dept: str
    klass: str
    fine: str
    classpath: str
    product_name: str
    verified: bool          # True only where ground truth confirms this exact leaf


CATEGORIES: dict[str, Category] = {
    "DISHWASHER": Category(
        "DISHWASHER", "Appliances", "Large Appliances", "Dishwashers",
        "Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers",
        "Dishwasher", verified=True),
    "WASHER": Category(
        "WASHER", "Appliances", "Large Appliances", "Washers",
        "Appliances & Consumer Electronics>Laundry Appliances>Clothes Washers",
        "Washer", verified=False),
    "DRYER": Category(
        "DRYER", "Appliances", "Large Appliances", "Dryers",
        "Appliances & Consumer Electronics>Laundry Appliances>Clothes Dryers",
        "Dryer", verified=False),
    "LAUNDRY_CENTER": Category(
        "LAUNDRY_CENTER", "Appliances", "Large Appliances", "Laundry Centers",
        "Appliances & Consumer Electronics>Laundry Appliances>Laundry Centers",
        "Laundry Center", verified=False),
    "RANGE": Category(
        "RANGE", "Appliances", "Large Appliances", "Ranges",
        "Appliances & Consumer Electronics>Kitchen Appliances>Ranges",
        "Range", verified=False),
    "COOKTOP": Category(
        "COOKTOP", "Appliances", "Large Appliances", "Cooktops",
        "Appliances & Consumer Electronics>Kitchen Appliances>Cooktops",
        "Cooktop", verified=False),
    "MICROWAVE": Category(
        "MICROWAVE", "Appliances", "Small Appliances", "Microwave Ovens",
        "Appliances & Consumer Electronics>Kitchen Appliances>Microwave Ovens",
        "Microwave Oven", verified=False),
    "REFRIGERATOR": Category(
        "REFRIGERATOR", "Appliances", "Large Appliances", "Refrigerators",
        "Appliances & Consumer Electronics>Kitchen Appliances>Refrigerators",
        "Refrigerator", verified=False),
    "FREEZER": Category(
        "FREEZER", "Appliances", "Large Appliances", "Freezers",
        "Appliances & Consumer Electronics>Kitchen Appliances>Freezers",
        "Freezer", verified=False),
}

# Order matters: more specific patterns first. "laundry center" before
# washer/dryer since a laundry center description also contains neither word
# reliably, and "range" before "cooktop"/"microwave" would be wrong if reordered
# given some descriptions mention multiple appliance nouns (e.g. a range grill
# accessory).
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"laundry\s*center", re.I), "LAUNDRY_CENTER"),
    (re.compile(r"\bdishwasher\b", re.I), "DISHWASHER"),
    (re.compile(r"\bdryer\b", re.I), "DRYER"),
    (re.compile(r"\bwasher\b", re.I), "WASHER"),
    (re.compile(r"\bcooktop\b", re.I), "COOKTOP"),
    (re.compile(r"\brange\b|\bgrill\b", re.I), "RANGE"),
    (re.compile(r"\bmicrowave\b", re.I), "MICROWAVE"),
    (re.compile(r"\bfreezer\b", re.I), "FREEZER"),
    (re.compile(r"\b(fridge|refrigerator)\b", re.I), "REFRIGERATOR"),
]


def classify(part_desc: str) -> Category | None:
    for pat, key in _RULES:
        if pat.search(part_desc):
            return CATEGORIES[key]
    return None
