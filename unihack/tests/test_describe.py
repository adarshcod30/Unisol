"""Every formula in describe.py was reverse-engineered from these two real
ground-truth rows (Solution Guide worked example + the Delivery Format CSV).
This test is the receipt: it must reproduce all five description fields
byte-for-byte from the real known attribute values, not just plausibly.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from unihack.describe import ProductFacts, ResolvedAttr, invoice_desc, mobile_desc, short_desc, retail_desc, long_desc1


def mk(label, value="", uom=""):
    return ResolvedAttr(label, value, uom)


FRIGIDAIRE = ProductFacts(
    "FRIGIDAIRE®", "Rheem Manufacturing", "PDSH4816AF", "Dishwasher",
    with_feature="CleanBoost™", attrs={
        "Series": mk("Series", "Professional Series"),
        "Number of Wash Cycles": mk("Number of Wash Cycles", "5"),
        "Voltage Rating": mk("Voltage Rating", "120", "V"),
        "Amperage Rating": mk("Amperage Rating", "15", "A"),
        "Mounting Type": mk("Mounting Type", "Leg"),
        "Size": mk("Size", "24 in W x 24-1/4 in D"),
        "Depth With Door Open": mk("Depth With Door Open", "50-1/4", "in"),
        "Minimum Height": mk("Minimum Height", "8-1/2 in Upper Rack, 11-1/4 in Lower Rack"),
        "Maximum Height": mk("Maximum Height", "10-3/8 in Upper Rack, 13-1/4 in Lower Rack"),
        "Sound Level": mk("Sound Level", "47", "dBA"),
        "Material": mk("Material", "Stainless Steel"),
        "Additional Information": mk("Additional Information",
            "240 kW-hr Annual Energy, 1 to 12 hr Delay Start Hours"),
    })

WHIRLPOOL = ProductFacts(
    "Whirlpool®", "Whirlpool Corporation", "WDTS7024RZ", "Dishwasher",
    with_feature="", attrs={
        "Series": mk("Series", "Eco Series"),
        "Voltage Rating": mk("Voltage Rating", "120", "V"),
        "Amperage Rating": mk("Amperage Rating", "10", "A"),
        "Mounting Type": mk("Mounting Type", "Built-in"),
        "Size": mk("Size", "33-7/16 in H x 23-7/8 in W x 22-5/8 in D"),
        "Depth With Door Open": mk("Depth With Door Open", "50-3/16", "in"),
        "Minimum Height": mk("Minimum Height", "33-7/16", "in"),
        "Sound Level": mk("Sound Level", "41", "dBA"),
        "Material": mk("Material", "Stainless Steel"),
        "Color": mk("Color", "Stainless Steel"),
        "Additional Information": mk("Additional Information",
            "Folding Tines, Leak Detection System, Moisture Repellent Silverware "
            "Basket, Normal Cycle, Quick Wash Cycle, Sani Rinse Option, Sensor "
            "Cycle, Triple Wash Spray"),
    })


def test_invoice_desc_matches_ground_truth():
    assert invoice_desc(FRIGIDAIRE)[0] == "DISHWASHER LEG 5 SST 120V 15A 50-1/4IN"
    assert invoice_desc(WHIRLPOOL)[0] == "DISHWASHER BLTLN SST SST 120V 10A 41DBA"


def test_invoice_desc_never_exceeds_40_chars():
    assert len(invoice_desc(FRIGIDAIRE)[0]) <= 40
    assert len(invoice_desc(WHIRLPOOL)[0]) <= 40


def test_mobile_desc_matches_ground_truth():
    assert mobile_desc(FRIGIDAIRE) == (
        "Rheem Manufacturing FRIGIDAIRE, Dishwasher, Professional Series, PDSH4816AF")
    assert mobile_desc(WHIRLPOOL) == (
        "Whirlpool, Dishwasher, Eco Series, WDTS7024RZ, Built-in Mounting")


def test_short_desc_matches_ground_truth():
    assert short_desc(FRIGIDAIRE) == (
        "FRIGIDAIRE® Professional Series PDSH4816AF Dishwasher With CleanBoost™, "
        "Leg Mounting, 5-Wash Cycle, Stainless Steel")
    assert short_desc(WHIRLPOOL) == (
        "Whirlpool® Eco Series WDTS7024RZ Dishwasher, Built-in Mounting, "
        "Stainless Steel, Stainless Steel")


def test_retail_desc_matches_ground_truth():
    assert retail_desc(FRIGIDAIRE) == (
        "Professional Series Dishwasher, Leg Mounting, 5-Wash Cycle, Stainless Steel")
    assert retail_desc(WHIRLPOOL) == (
        "Eco Series Dishwasher, Built-in Mounting, Stainless Steel, Stainless Steel")


def test_long_desc1_matches_ground_truth():
    assert long_desc1(FRIGIDAIRE) == (
        "FRIGIDAIRE® Dishwasher With CleanBoost™, Professional Series, 5 Wash "
        "Cycles, 120 V, 15 A, Leg Mounting, 24 in W x 24-1/4 in D, 50-1/4 in "
        "Depth With Door Open, 8-1/2 in Upper Rack, 11-1/4 in Lower Rack Minimum "
        "Height, 10-3/8 in Upper Rack, 13-1/4 in Lower Rack Maximum Height, 47 "
        "dBA Sound Level, Stainless Steel, Additional Information: 240 kW-hr "
        "Annual Energy, 1 to 12 hr Delay Start Hours")
    assert long_desc1(WHIRLPOOL) == (
        "Whirlpool® Dishwasher, Eco Series, 120 V, 10 A, Built-in Mounting, "
        "33-7/16 in H x 23-7/8 in W x 22-5/8 in D, 50-3/16 in Depth With Door "
        "Open, 33-7/16 in Minimum Height, 41 dBA Sound Level, Stainless Steel, "
        "Stainless Steel, Additional Information: Folding Tines, Leak Detection "
        "System, Moisture Repellent Silverware Basket, Normal Cycle, Quick Wash "
        "Cycle, Sani Rinse Option, Sensor Cycle, Triple Wash Spray")
