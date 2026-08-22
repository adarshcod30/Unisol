"""Per-category attribute templates.

DISHWASHER is verified: the exact attribute set, order and labels below are
copied from the two known-good ground-truth rows, not guessed. WASHER and DRYER
are best-effort extensions built from the same shape (voltage/amperage/mounting/
dimensions/material are near-universal major-appliance attributes) but are NOT
checked against any ground truth, and are marked unverified so the pipeline can
apply a stricter confidence floor to them.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AttrSpec:
    label: str
    unit: str | None = None       # expected UOM key, or None for unitless/text
    aliases: tuple = ()
    verified: bool = True


DISHWASHER = [
    AttrSpec("Series", aliases=("series",)),
    AttrSpec("Model", aliases=("model",)),
    AttrSpec("Number of Wash Cycles", aliases=("wash cycles", "number of cycles")),
    AttrSpec("Voltage Rating", unit="V", aliases=("voltage", "rated voltage")),
    AttrSpec("Amperage Rating", unit="A", aliases=("amperage", "amps", "rated amperage")),
    AttrSpec("Mounting Type", aliases=("mounting", "installation type")),
    AttrSpec("Plug Type", aliases=("plug",)),
    AttrSpec("Size", aliases=("dimensions", "overall dimensions")),
    AttrSpec("Depth With Door Open", unit="in", aliases=("depth with door open",)),
    AttrSpec("Minimum Height", unit="in", aliases=("minimum height", "min height")),
    AttrSpec("Maximum Height", unit="in", aliases=("maximum height", "max height")),
    AttrSpec("Sound Level", unit="dBA", aliases=("sound level", "noise level", "decibel")),
    AttrSpec("Material", aliases=("material", "finish material")),
    AttrSpec("Color", aliases=("color", "colour")),
    AttrSpec("Additional Information", aliases=("features", "additional features")),
]

# Best-effort, unverified: same general shape, adapted for laundry appliances.
WASHER = [
    AttrSpec("Series", aliases=("series",), verified=False),
    AttrSpec("Model", aliases=("model",), verified=False),
    AttrSpec("Capacity", unit="cu ft", aliases=("capacity", "tub capacity"), verified=False),
    AttrSpec("Voltage Rating", unit="V", aliases=("voltage",), verified=False),
    AttrSpec("Amperage Rating", unit="A", aliases=("amperage",), verified=False),
    AttrSpec("Load Type", aliases=("load type", "top load", "front load"), verified=False),
    AttrSpec("Size", aliases=("dimensions",), verified=False),
    AttrSpec("Sound Level", unit="dBA", aliases=("sound level",), verified=False),
    AttrSpec("Material", aliases=("material",), verified=False),
    AttrSpec("Color", aliases=("color",), verified=False),
    AttrSpec("Additional Information", aliases=("features",), verified=False),
]

DRYER = [
    AttrSpec("Series", aliases=("series",), verified=False),
    AttrSpec("Model", aliases=("model",), verified=False),
    AttrSpec("Capacity", unit="cu ft", aliases=("capacity",), verified=False),
    AttrSpec("Fuel Type", aliases=("fuel type", "gas", "electric"), verified=False),
    AttrSpec("Voltage Rating", unit="V", aliases=("voltage",), verified=False),
    AttrSpec("Amperage Rating", unit="A", aliases=("amperage",), verified=False),
    AttrSpec("Size", aliases=("dimensions",), verified=False),
    AttrSpec("Material", aliases=("material",), verified=False),
    AttrSpec("Color", aliases=("color",), verified=False),
    AttrSpec("Additional Information", aliases=("features",), verified=False),
]

BY_CATEGORY = {
    "DISHWASHER": DISHWASHER,
    "WASHER": WASHER,
    "DRYER": DRYER,
}


def attrs_for(category_key: str) -> list[AttrSpec]:
    return BY_CATEGORY.get(category_key, [])
