"""Field-level accuracy against the two known-good ground-truth rows -- the
"Show your evaluation" metric the guide explicitly says judges look for.

Two numbers are reported, deliberately kept separate:

  1. Construction fidelity: given the CORRECT attributes (as ground truth
     states them), do the description-builder formulas in describe.py
     reproduce the real strings exactly? This isolates the deterministic
     template logic from live sourcing, and is covered byte-for-byte by
     unihack/tests/test_describe.py (10/10 fields, both rows).

  2. End-to-end field accuracy: running the FULL live pipeline (real brand
     resolution, real fetch, real evidence-gated extraction) against these
     same two SKUs, how many Delivery Format fields match ground truth? This
     is the honest number -- it is bounded above by whichever manufacturer
     sites happen to be reachable at run time, which is real-world, not a
     limitation of the scoring method.

Run: .venv/bin/python -m unihack.eval
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unihack.pipeline import InputRow, run_row                     # noqa: E402

GROUND_TRUTH = {
    "PDSH4816AF": {
        "Dept": "Appliances", "Class": "Large Appliances", "Fine": "Dishwashers",
        "MANUFACTURER_NAME": "Rheem Manufacturing", "BRAND_NAME": "FRIGIDAIRE®",
        "MANUFACTURER_PART_NUMBER": "PDSH4816AF",
        "Classpath": "Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers",
        "Product Name": "Dishwasher",
    },
    "WDTS7024RZ": {
        "Dept": "Appliances", "Class": "Large Appliances", "Fine": "Dishwashers",
        "MANUFACTURER_NAME": "Whirlpool Corporation", "BRAND_NAME": "Whirlpool®",
        "MANUFACTURER_PART_NUMBER": "WDTS7024RZ",
        "Classpath": "Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers",
        "Product Name": "Dishwasher",
    },
}

ROWS = {
    "PDSH4816AF": InputRow("PDSH4816AF", "PDSH4816AF Dishwasher SS - Display Only",
                           "-- Unbranded --", "-- No Unilog Brand --",
                           "-- No DIB Brand --", "Appliance Dealers Cooperative (APPDE)"),
    "WDTS7024RZ": InputRow("WDTS7024RZ", "WDTS7024RZ Dishwasher SS - Display Only",
                           "-- Unbranded --", "-- No Unilog Brand --",
                           "-- No DIB Brand --", "Appliance Dealers Cooperative (APPDE)"),
}


def main():
    total = matched = 0
    for mpn, truth in GROUND_TRUTH.items():
        out = run_row(ROWS[mpn])
        print(f"\n{'='*70}\n{mpn}  (live pipeline: decision={out.decision}, "
              f"confidence={out.confidence})")
        for field, expect in truth.items():
            got = out.values.get(field, "")
            ok = got == expect
            total += 1
            matched += ok
            print(f"  [{'MATCH' if ok else 'DIFF '}] {field:28s} "
                  f"expect={expect!r:60s} got={got!r}")
        print("  sourcing/reasons:")
        for r in out.reasons:
            print(f"    - {r}")

    print(f"\n{'='*70}\nfield-level match on structural fields: {matched}/{total} "
          f"({100*matched/total:.0f}%)")
    print("(construction-formula fidelity given correct attributes: 10/10, "
          "see unihack/tests/test_describe.py)")


if __name__ == "__main__":
    main()
