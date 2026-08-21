"""The input catalog: what a distributor actually starts with.

This is the "limited product information" the challenge describes -- a part
number, a brand, and a short marketing line. No structured attributes at all.
Everything else in the record has to be earned from source documents.
"""
from __future__ import annotations
from dataclasses import dataclass

from . import corpus


@dataclass
class InputSKU:
    sku: str
    mpn: str
    brand: str
    description: str
    product_class: str


CATALOG: list[InputSKU] = [
    InputSKU("DIO-1N4001-VSH", "1N4001", "Vishay",
             "General purpose plastic rectifier diode, axial leaded", "RECTIFIER_DIODE"),
    InputSKU("DIO-1N4002-VSH", "1N4002", "Vishay",
             "General purpose rectifier diode", "RECTIFIER_DIODE"),
    InputSKU("DIO-1N4004-VSH", "1N4004", "Vishay",
             "Silicon rectifier diode DO-41", "RECTIFIER_DIODE"),
    InputSKU("DIO-1N4007-VSH", "1N4007", "Vishay",
             "High voltage general purpose rectifier", "RECTIFIER_DIODE"),
    InputSKU("DIO-1N4001-DIO", "1N4001", "Diodes Incorporated",
             "Rectifier diode 1A axial", "RECTIFIER_DIODE"),
    InputSKU("DIO-1N4007-DIO", "1N4007", "Diodes Incorporated",
             "Rectifier diode, plastic package", "RECTIFIER_DIODE"),
    InputSKU("DIO-1N5817-VSH", "1N5817", "Vishay",
             "Schottky barrier rectifier low drop", "RECTIFIER_DIODE"),
    InputSKU("DIO-1N5819-VSH", "1N5819", "Vishay",
             "Schottky barrier plastic rectifier", "RECTIFIER_DIODE"),
    InputSKU("REG-LM317-TI", "LM317", "Texas Instruments",
             "Adjustable positive voltage regulator 3 pin", "LINEAR_REGULATOR"),
    InputSKU("REG-LM317L-TI", "LM317L", "Texas Instruments",
             "Adjustable floating voltage regulator low current", "LINEAR_REGULATOR"),
    InputSKU("REG-LM1117-TI", "LM1117", "Texas Instruments",
             "Low dropout linear regulator adjustable", "LINEAR_REGULATOR"),
    InputSKU("REG-LM2940-TI", "LM2940", "Texas Instruments",
             "Low dropout voltage regulator automotive", "LINEAR_REGULATOR"),
]

BY_SKU = {s.sku: s for s in CATALOG}


def known_parts() -> set[str]:
    """Every part number we know to be a distinct product.

    Used by the contamination guard: a mention of a *known other* part inside an
    evidence window means that sentence is not about the part we're enriching.
    """
    parts = {s.mpn.upper() for s in CATALOG}
    for m in corpus.MANIFEST:
        parts |= {c.upper() for c in m.covers}
    return parts


def sources_for(sku: InputSKU) -> list[str]:
    return corpus.docs_for_part(sku.mpn)
