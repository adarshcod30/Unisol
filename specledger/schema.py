"""Per-class attribute schemas.

The product class decides which attributes exist, what units they carry, and which
are safety-critical. This is what makes extraction targeted instead of open-ended:
we never ask an LLM "what are the specs?", we ask "what is reverse_voltage_max, in
volts, and where exactly does it say that?".
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AttributeSpec:
    name: str
    label: str
    dtype: str                       # number | enum | bool | string
    unit: Optional[str] = None       # canonical unit (pint-parseable)
    enum: tuple = ()
    required: bool = False
    safety_critical: bool = False
    aliases: tuple = ()              # how datasheets actually name it
    plausible_min: Optional[float] = None
    plausible_max: Optional[float] = None
    hint: str = ""                   # extra instruction for the extractor
    allow_sections: tuple = ()       # sections permitted to state this attribute.
                                     # Empty = any section except chart regions.
    is_range_end: str = ""           # "" | "min" | "max" -- only true two-ended
                                     # ranges ("-65 to +150 degC") may use the
                                     # RangeExtractor. A "maximum output current"
                                     # is a bound, not a range end.

    def in_plausible_range(self, v: float) -> bool:
        if self.plausible_min is not None and v < self.plausible_min:
            return False
        if self.plausible_max is not None and v > self.plausible_max:
            return False
        return True


@dataclass
class ProductClass:
    key: str
    label: str
    unspsc: str = ""
    attributes: list[AttributeSpec] = field(default_factory=list)

    def by_name(self, name: str) -> Optional[AttributeSpec]:
        for a in self.attributes:
            if a.name == name:
                return a
        return None

    @property
    def names(self) -> list[str]:
        return [a.name for a in self.attributes]


_TEMP_MIN = AttributeSpec(
    "operating_temp_min", "Operating Temperature (Min)", "number", "degC",
    aliases=("operating temperature", "operating junction temperature", "T_A", "TJ", "storage temperature"),
    plausible_min=-100, plausible_max=50, is_range_end="min",
    allow_sections=("ABS_MAX", "RECOMMENDED", "FEATURES"),
    hint="The LOW end of the operating temperature range, e.g. -55 in '-55 to 150 degC'.",
)
_TEMP_MAX = AttributeSpec(
    "operating_temp_max", "Operating Temperature (Max)", "number", "degC",
    aliases=("operating temperature", "operating junction temperature", "T_A", "TJ"),
    plausible_min=25, plausible_max=300, is_range_end="max",
    allow_sections=("ABS_MAX", "RECOMMENDED", "FEATURES"),
    hint="The HIGH end of the operating temperature range, e.g. 150 in '-55 to 150 degC'.",
)
_PACKAGE = AttributeSpec(
    "package", "Package / Case", "string",
    aliases=("package", "case", "case style", "outline", "package type"),
    required=True, allow_sections=("MECHANICAL", "FEATURES"),
    hint="The physical package designator, e.g. TO-220, DO-41, SOIC-8, TO-92, SOT-23.",
)
_MOUNT = AttributeSpec(
    "mounting_type", "Mounting Type", "enum",
    enum=("THROUGH_HOLE", "SURFACE_MOUNT"),
    aliases=("mounting", "mount", "through hole", "surface mount", "SMD", "SMT"),
)

RECTIFIER_DIODE = ProductClass(
    key="RECTIFIER_DIODE",
    label="Rectifier Diode",
    unspsc="32111600",
    attributes=[
        AttributeSpec(
            "reverse_voltage_max", "Peak Repetitive Reverse Voltage", "number", "V",
            required=True, safety_critical=True,
            aliases=("VRRM", "peak repetitive reverse voltage", "maximum repetitive peak reverse voltage",
                     "reverse voltage", "VR", "working peak reverse voltage"),
            plausible_min=10, plausible_max=2000, allow_sections=("ABS_MAX", "FEATURES", "RECOMMENDED"),
            hint=("CRITICAL: series datasheets tabulate this per part number across a row or column. "
                  "You must read the value in the column for the EXACT part number requested, not the "
                  "highest value in the table."),
        ),
        AttributeSpec(
            "forward_current_avg", "Average Rectified Forward Current", "number", "A",
            required=True, safety_critical=True,
            aliases=("IF(AV)", "average rectified forward current", "forward current", "IO"),
            plausible_min=0.01, plausible_max=100, allow_sections=("ABS_MAX", "FEATURES", "RECOMMENDED"),
        ),
        AttributeSpec(
            "forward_voltage_max", "Forward Voltage Drop", "number", "V",
            aliases=("VF", "forward voltage", "instantaneous forward voltage"),
            plausible_min=0.1, plausible_max=5,
            allow_sections=("ABS_MAX", "FEATURES", "ELEC_CHAR"),
        ),
        AttributeSpec(
            "surge_current_max", "Peak Forward Surge Current", "number", "A",
            safety_critical=True,
            aliases=("IFSM", "peak forward surge current", "surge current", "non-repetitive peak forward surge"),
            plausible_min=1, plausible_max=1000, allow_sections=("ABS_MAX", "FEATURES", "RECOMMENDED"),
        ),
        _TEMP_MIN, _TEMP_MAX, _PACKAGE, _MOUNT,
        AttributeSpec(
            "rohs_compliant", "RoHS Compliant", "bool",
            aliases=("RoHS", "lead free", "Pb-free", "halogen free"),
        ),
    ],
)

LINEAR_REGULATOR = ProductClass(
    key="LINEAR_REGULATOR",
    label="Linear Voltage Regulator",
    unspsc="32101600",
    attributes=[
        AttributeSpec(
            "output_current_max", "Maximum Output Current", "number", "A",
            required=True, safety_critical=True,
            aliases=("output current", "IO", "maximum output current", "load current", "IOUT"),
            plausible_min=0.01, plausible_max=20, allow_sections=("ABS_MAX", "FEATURES", "RECOMMENDED"),
            hint=("CRITICAL: suffix variants differ hugely (e.g. a base part at 1.5 A vs an 'L' suffix "
                  "variant at 0.1 A). Read the value for the EXACT part number requested."),
        ),
        AttributeSpec(
            "input_voltage_max", "Maximum Input Voltage", "number", "V",
            required=True, safety_critical=True,
            aliases=("input voltage", "VI", "VIN", "maximum input voltage", "input-to-output differential"),
            plausible_min=1, plausible_max=100, allow_sections=("ABS_MAX", "FEATURES", "RECOMMENDED"),
        ),
        AttributeSpec(
            "output_voltage_min", "Output Voltage (Min)", "number", "V",
            aliases=("output voltage range", "VO", "VOUT", "adjustable"),
            plausible_min=0.5, plausible_max=50, is_range_end="min",
        ),
        AttributeSpec(
            "output_voltage_max", "Output Voltage (Max)", "number", "V",
            aliases=("output voltage range", "VO", "VOUT", "adjustable"),
            plausible_min=1, plausible_max=60, is_range_end="max",
        ),
        AttributeSpec(
            "dropout_voltage_typ", "Dropout Voltage (Typ)", "number", "V",
            aliases=("dropout voltage", "input-output differential", "VDO"),
            plausible_min=0.05, plausible_max=5,
        ),
        AttributeSpec(
            "adjustable", "Adjustable Output", "bool",
            aliases=("adjustable", "programmable output", "fixed output"),
        ),
        AttributeSpec(
            "polarity", "Regulator Polarity", "enum", enum=("POSITIVE", "NEGATIVE"),
            aliases=("positive voltage regulator", "negative voltage regulator", "polarity"),
        ),
        _TEMP_MIN, _TEMP_MAX, _PACKAGE, _MOUNT,
    ],
)

REGISTRY: dict[str, ProductClass] = {
    c.key: c for c in (RECTIFIER_DIODE, LINEAR_REGULATOR)
}


def get(key: str) -> ProductClass:
    if key not in REGISTRY:
        raise KeyError(f"unknown product class {key!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[key]


def all_safety_critical() -> set[str]:
    out = set()
    for c in REGISTRY.values():
        out |= {a.name for a in c.attributes if a.safety_critical}
    return out
