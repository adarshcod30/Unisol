"""The failure modes that make industrial spec data dangerous.

Each of these is a real trap in a real vendor PDF, not a synthetic fixture.
Publishing any of these wrong has physical consequences: a 50 V diode installed
on a 600 V rail fails short, and a 100 mA regulator asked for 1.5 A burns.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from specledger import corpus, schema
from specledger.catalog import BY_SKU, known_parts
from specledger.confidence import ConfidenceModel
from specledger.extract import extract_from_doc
from specledger.ingest import ingest
from specledger.extract import DETERMINISTIC
from specledger.pipeline import enrich


def resolve(doc_id, mpn, pclass_key, attribute):
    d = ingest(doc_id)
    cands = extract_from_doc(d, mpn, schema.get(pclass_key),
                            corpus.BY_ID[doc_id].covers, known_parts())
    live = [c for c in cands if c.attribute == attribute and c.usable
            and not c.contaminated_by and not c.dropped_reason]
    assert live, f"no usable candidate for {attribute} of {mpn}"
    return max(live, key=lambda c: c.precision)


@pytest.mark.parametrize("mpn,expected", [
    ("1N4001", 50.0), ("1N4002", 100.0), ("1N4004", 400.0), ("1N4007", 1000.0),
])
def test_series_column_vishay(mpn, expected):
    """One row, seven parts, 50 V to 1000 V. The column index IS the answer."""
    c = resolve("vishay-1n4001-4007", mpn, "RECTIFIER_DIODE", "reverse_voltage_max")
    assert c.value == expected
    assert c.column_index >= 0, "must resolve a specific column, not a shared value"


@pytest.mark.parametrize("mpn,expected", [("1N4001", 50.0), ("1N4007", 1000.0)])
def test_series_column_diodes_inc(mpn, expected):
    """Same trap, different vendor, different table layout."""
    c = resolve("diodes-1n4001-4007", mpn, "RECTIFIER_DIODE", "reverse_voltage_max")
    assert c.value == expected


@pytest.mark.parametrize("mpn,expected", [("1N5817", 20.0), ("1N5819", 40.0)])
def test_series_column_schottky(mpn, expected):
    c = resolve("vishay-1n5817-5819", mpn, "RECTIFIER_DIODE", "reverse_voltage_max")
    assert c.value == expected


def test_lm317_output_current():
    c = resolve("ti-lm317", "LM317", "LINEAR_REGULATOR", "output_current_max")
    assert c.value == 1.5


def test_lm317l_is_not_poisoned_by_its_own_sibling_reference():
    """The LM317L datasheet contains 'see LM317M (500mA) and LM317 (1.5A)'.

    A naive extractor searching for 'output current' matches that sentence and
    publishes 1.5 A for a 100 mA part -- a 15x overstatement on a safety-critical
    rating. The contamination guard must quarantine it.
    """
    d = ingest("ti-lm317l")
    cands = extract_from_doc(d, "LM317L", schema.get("LINEAR_REGULATOR"),
                            corpus.BY_ID["ti-lm317l"].covers, known_parts())
    cur = [c for c in cands if c.attribute == "output_current_max"]
    poisoned = [c for c in cur if c.value == 1.5 or c.value == 0.5]
    for c in poisoned:
        assert c.contaminated_by, f"{c.value} A should be quarantined, cited: {c.evidence.quote!r}"
    clean = [c for c in cur if c.usable and not c.contaminated_by and not c.dropped_reason]
    assert clean and max(clean, key=lambda c: c.precision).value == 0.1


def test_end_to_end_never_publishes_a_wrong_reverse_voltage():
    model = ConfidenceModel.load()
    truth = {"DIO-1N4001-VSH": 50.0, "DIO-1N4002-VSH": 100.0,
             "DIO-1N4004-VSH": 400.0, "DIO-1N4007-VSH": 1000.0,
             "DIO-1N5817-VSH": 20.0, "DIO-1N5819-VSH": 40.0}
    for sku, want in truth.items():
        # DETERMINISTIC pins this test to the regex/table extractors regardless
        # of whether LLM credentials happen to be configured in the environment --
        # tests must be hermetic, not dependent on ambient .env state.
        rec = enrich(BY_SKU[sku], model, extractors=DETERMINISTIC)
        a = rec.attributes.get("reverse_voltage_max")
        assert a is not None, sku
        if a.decision == "AUTO_PUBLISH":
            assert a.value == want, f"{sku} published {a.value}, expected {want}"
