"""Datasheet section segmentation.

Every vendor lays a datasheet out the same way, and each section carries a
different kind of authority:

  ABS_MAX         Absolute Maximum Ratings -- destroy-the-part limits. Authoritative.
  RECOMMENDED     Recommended Operating Conditions -- guaranteed operating window.
  ELEC_CHAR       Electrical Characteristics -- measured values, and TEST CONDITIONS
                  ("TJ = 100 degC") that read exactly like ratings but are not.
  TYPICAL_GRAPHS  Typical Characteristics -- plots. The extractable text here is
                  AXIS LABELS and tick values. It is authoritative for nothing.
  FEATURES        Front-page summary / PRIMARY CHARACTERISTICS. Usually correct,
                  but unqualified by part variant.
  MECHANICAL      Package, ordering, tape-and-reel.
  THERMAL         Thermal Information -- degC/W resistances, never temperatures.

Knowing which section a quote came from removes a whole class of misattribution
that no amount of regex tuning can reach, and it gives a reviewer a provenance
line they immediately trust: "read from Absolute Maximum Ratings, page 6".
"""
from __future__ import annotations

import re
from bisect import bisect_right

SECTION_PATTERNS: list[tuple[str, str]] = [
    ("ABS_MAX", r"absolute\s+maximum\s+ratings"
                r"|maximum\s+ratings\s+and\s+electrical\s+characteristics"
                r"|(?<![a-z])maximum\s+ratings(?:\s*\(|\s*\n)"),
    ("RECOMMENDED", r"recommended\s+operating\s+conditions"),
    ("THERMAL", r"thermal\s+information|thermal\s+resistance\s+table"),
    ("TYPICAL_GRAPHS", r"typical\s+characteristics|typical\s+performance\s+characteristics"),
    ("ELEC_CHAR", r"electrical\s+characteristics|characteristics\s+\(\s*t[aj]\s*="),
    ("FEATURES", r"primary\s+characteristics|(?:^|\n)\s*\d?\s*features(?:\s|\n)"),
    ("MECHANICAL", r"mechanical\s+data|ordering\s+information|package\s+(?:option|information|outline|materials)|tape\s+and\s+reel"),
]
_COMPILED = [(k, re.compile(p, re.IGNORECASE)) for k, p in SECTION_PATTERNS]
_TOC_LEADER = re.compile(r"\.{3,}|\n\s*\d{1,3}\s*\n\s*\d")

# Chart axis labels: a name followed by a parenthesised unit. Vishay and Diodes
# print their characteristic curves with no section heading at all, so headings
# cannot find them -- but an axis label is unmistakable, and the numbers beside
# one are tick marks, not ratings.
AXIS_LABEL_RE = re.compile(
    r"[A-Za-z][A-Za-z ,.\-]{4,44}\(\s*(?:V|A|mA|\u03bcA|uA|pF|nF|W|mW|\u00b0C|s|ms|\u03bcs|Hz)\s*\)")

DEFAULT = "OTHER"


def _is_toc_entry(text: str, pos: int, end: int) -> bool:
    """Table-of-contents lines match every heading pattern. They are followed by a
    dotted leader and a page number; real headings are followed by content."""
    tail = text[end: end + 70]
    return bool(_TOC_LEADER.search(tail))


def segment(text: str) -> list[tuple[int, str]]:
    """-> sorted [(char_offset, section_key)] marking where each section begins."""
    marks: list[tuple[int, str]] = []
    for key, rx in _COMPILED:
        for m in rx.finditer(text):
            if _is_toc_entry(text, m.start(), m.end()):
                continue
            marks.append((m.start(), key))
    marks.sort()
    # "Maximum Ratings and Electrical Characteristics" matches both patterns.
    # The ratings claim wins: that table holds ratings, not measurements.
    absmax = [o for o, k in marks if k == "ABS_MAX"]
    marks = [(o, k) for o, k in marks
             if not (k == "ELEC_CHAR" and any(0 <= o - a <= 60 for a in absmax))]
    deduped: list[tuple[int, str]] = []
    for off, key in marks:
        if deduped and deduped[-1][1] == key and off - deduped[-1][0] < 40:
            continue
        deduped.append((off, key))
    return deduped


class SectionMap:
    def __init__(self, text: str):
        self._text = text
        self.marks = segment(text)
        self._offsets = [o for o, _ in self.marks]

    def at(self, offset: int) -> str:
        if not self.marks:
            return DEFAULT
        i = bisect_right(self._offsets, offset) - 1
        return self.marks[i][1] if i >= 0 else DEFAULT

    def is_graph_region(self, offset: int, radius: int = 300) -> bool:
        """True when an offset sits among chart axis labels.

        Two or more axis labels in close proximity is a plot, not a table. This
        is what keeps 'Instantaneous Forward Voltage (V) 0 0.4 0.2' from being
        read as a forward-voltage rating of 0.2 V.
        """
        w = self._text[max(0, offset - radius): offset + radius]
        return len(AXIS_LABEL_RE.findall(w)) >= 2

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, k in self.marks:
            out[k] = out.get(k, 0) + 1
        return out
