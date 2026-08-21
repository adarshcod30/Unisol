"""Cross-attribute plausibility rules.

Single-attribute range checks catch nonsense in isolation. These catch the errors
that only show up in combination -- a regulator whose maximum input is below its
maximum output, a rectifier whose surge rating is under its continuous rating.
Physics is a free, model-independent validator and industrial data is full of it.
"""
from __future__ import annotations
from typing import Callable

from .schema import ProductClass


class Rule:
    def __init__(self, key: str, applies_to: tuple, message: str,
                 fn: Callable[[dict], bool], severity: str = "error"):
        self.key, self.applies_to, self.message, self.fn = key, applies_to, message, fn
        self.severity = severity

    def check(self, values: dict) -> bool:
        """True if the rule HOLDS (or cannot be evaluated)."""
        if any(values.get(a) is None for a in self.applies_to):
            return True
        try:
            return self.fn(values)
        except Exception:
            return True


RULES: dict[str, list[Rule]] = {
    "RECTIFIER_DIODE": [
        Rule("surge_ge_continuous", ("surge_current_max", "forward_current_avg"),
             "peak surge current must exceed average forward current",
             lambda v: v["surge_current_max"] > v["forward_current_avg"]),
        Rule("vf_below_vrrm", ("forward_voltage_max", "reverse_voltage_max"),
             "forward drop must be far below reverse standoff voltage",
             lambda v: v["forward_voltage_max"] < v["reverse_voltage_max"]),
        Rule("temp_ordered", ("operating_temp_min", "operating_temp_max"),
             "minimum operating temperature must be below maximum",
             lambda v: v["operating_temp_min"] < v["operating_temp_max"]),
        Rule("silicon_vf_band", ("forward_voltage_max",),
             "silicon rectifier forward drop outside 0.2-2.0 V is implausible",
             lambda v: 0.2 <= v["forward_voltage_max"] <= 2.0, severity="warn"),
    ],
    "LINEAR_REGULATOR": [
        Rule("vin_above_vout", ("input_voltage_max", "output_voltage_max"),
             "maximum input voltage must exceed maximum output voltage",
             lambda v: v["input_voltage_max"] > v["output_voltage_max"]),
        Rule("vout_ordered", ("output_voltage_min", "output_voltage_max"),
             "minimum output voltage must be below maximum",
             lambda v: v["output_voltage_min"] < v["output_voltage_max"]),
        Rule("temp_ordered", ("operating_temp_min", "operating_temp_max"),
             "minimum operating temperature must be below maximum",
             lambda v: v["operating_temp_min"] < v["operating_temp_max"]),
        Rule("dropout_sane", ("dropout_voltage_typ", "input_voltage_max"),
             "dropout voltage must be a small fraction of maximum input",
             lambda v: v["dropout_voltage_typ"] < v["input_voltage_max"] / 2,
             severity="warn"),
    ],
}


def evaluate(pclass_key: str, values: dict) -> dict[str, list[str]]:
    """-> {attribute_name: [violated rule messages]}"""
    out: dict[str, list[str]] = {}
    for rule in RULES.get(pclass_key, []):
        if not rule.check(values):
            for a in rule.applies_to:
                out.setdefault(a, []).append(f"[{rule.severity}] {rule.message}")
    return out


def range_violations(pclass: ProductClass, values: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for spec in pclass.attributes:
        v = values.get(spec.name)
        if v is None or spec.dtype != "number":
            continue
        if not spec.in_plausible_range(float(v)):
            out.setdefault(spec.name, []).append(
                f"[error] {v} outside plausible range "
                f"[{spec.plausible_min}, {spec.plausible_max}] for {spec.label}")
    return out
