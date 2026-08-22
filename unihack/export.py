"""Write the exact 252-column Delivery Format CSV. Headers are never renamed,
reordered, added, or removed -- copied verbatim from the real Expected Output
file, per the brief's explicit instruction.
"""
from __future__ import annotations

import csv
from pathlib import Path

from unihack.data.output_header import HEADER
from unihack.pipeline import OutputRow


def write_csv(rows: list[OutputRow], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow([r.values.get(h, "") for h in HEADER])


def write_review_log(rows: list[tuple[str, OutputRow]], path: str | Path) -> None:
    """A separate, human-facing companion file: which MPN, what confidence,
    why it needs a look. Not part of the required 252-column deliverable, but
    directly the "needs human review" feature the guide calls out as a
    genuinely valuable thing to surface."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Mfg_Part_Num", "decision", "confidence", "reasons"])
        for mpn, r in rows:
            w.writerow([mpn, r.decision, r.confidence, " | ".join(r.reasons)])
