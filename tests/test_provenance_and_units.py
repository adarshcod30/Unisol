"""Section authority, unit handling, and the abstention contract."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from specledger.ingest import ingest
from specledger.normalize import compatible, normalize_value
from specledger.schema import LINEAR_REGULATOR, RECTIFIER_DIODE
from specledger.sections import SectionMap


def test_graph_axis_regions_are_detected():
    """Vishay prints characteristic curves with no heading at all; the numbers
    beside an axis label are tick marks, never ratings."""
    d = ingest("vishay-1n4001-4007")
    sm = SectionMap(d.text)
    axis = d.text.find("Instantaneous Forward Voltage")
    assert axis > 0
    assert sm.is_graph_region(axis)


def test_ratings_table_is_not_a_graph_region():
    d = ingest("vishay-1n4001-4007")
    sm = SectionMap(d.text)
    i = d.text.find("Operating junction and")
    assert sm.at(i) == "ABS_MAX"
    assert not sm.is_graph_region(i)


def test_units_convert_to_the_schema_canonical_unit():
    spec = LINEAR_REGULATOR.by_name("output_current_max")
    assert normalize_value("100mA", spec)[0] == 0.1
    assert normalize_value("1.5A", spec)[0] == 1.5
    assert normalize_value("Up to 100 mA", spec)[0] == 0.1


def test_dimensional_guard_rejects_the_wrong_quantity():
    """'Output current' can never be volts, however plausible the sentence."""
    spec = LINEAR_REGULATOR.by_name("output_current_max")
    value, _, _, err = normalize_value("1.25V", spec)
    assert value is None and "not compatible" in err
    assert not compatible("V", "A")


def test_implausible_values_are_refused():
    spec = RECTIFIER_DIODE.by_name("reverse_voltage_max")
    assert not spec.in_plausible_range(99999)
    assert spec.in_plausible_range(50)


def test_every_numeric_attribute_declares_a_plausible_range():
    """A rating with no bounds cannot be sanity-checked, so the schema requires one."""
    for pclass in (RECTIFIER_DIODE, LINEAR_REGULATOR):
        for a in pclass.attributes:
            if a.dtype == "number":
                assert a.plausible_min is not None and a.plausible_max is not None, a.name
