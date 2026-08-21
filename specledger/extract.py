"""Candidate generation.

Every extractor obeys one contract: emit a Candidate carrying a verbatim quote and
character offsets, or emit nothing. Abstention is always allowed and never
penalised -- a missing attribute costs a review click, a wrong one costs a recall.
These extractors are precision-first by construction.

  TableColumnExtractor  Series datasheets tabulate N part numbers across a header
                        row; the value for THIS part sits at THIS column index.
                        Defeats the 1N4001-vs-1N4007 trap.
  RangeExtractor        "-65 to +150 degC" two-ended ranges (schema-gated).
  InlineSpecExtractor   "Output current: 1.5A" label/value lines, guarded by a
                        sibling-contamination check and a dimensional check.
  LLMExtractor          Claude structured output, held to the same evidence
                        contract. Used when an API key is present.
"""
from __future__ import annotations

import re
from typing import Optional

from .ingest import IngestedDoc, collapse
from .models import Candidate, EvidenceSpan
from .normalize import NUMBER_RE, compatible, normalize_candidate
from .schema import AttributeSpec, ProductClass

PART_RE = re.compile(r"\b(?:1N|2N|LM|TL|UA|MC|BC|BAV|MMBT|SS|SR|NE)\d{2,5}[A-Z]{0,4}\b")
PACKAGE_RE = re.compile(
    r"\b(?:DO|TO|SOT|SOD|SOIC|TSSOP|MSOP|DPAK|QFN|SC|DIP|PDIP)-?\d{1,4}[A-Z]{0,3}\b",
    re.IGNORECASE)
PURE_NUMBER_RE = re.compile(r"^[-+−]?\d+(?:[.,]\d+)?$")
UNIT_ONLY_RE = re.compile(
    r"^(?:°\s?[CF]|[munpkKM]?(?:A|V|W|F|s|Hz)|Ω|ohm|k/W|pF|nF|μA|µA|uA|dB|%|℃)$",
    re.IGNORECASE)
TOKEN_RE = re.compile(r"\S+")
_U = r"(°\s?[CF]|℃|[munpkKM]?[AVWF]|degC|degF)"
RANGE_RE = re.compile(
    rf"([-+−]?\d+(?:\.\d+)?)\s*{_U}?\s*(?:to|\.\.\.|…|~|–|—)\s*"
    rf"([-+−]?\d+(?:\.\d+)?)\s*{_U}?",
    re.IGNORECASE)

# Higher is better. Arbitration prefers evidence from a more precise strategy.
EXTRACTOR_PRECISION = {"table_column": 3, "range": 2, "inline_spec": 1, "llm": 2}


def part_tokens(text: str) -> set[str]:
    return {m.group(0).upper() for m in PART_RE.finditer(text)}


def masked_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges that are part numbers or package codes.

    Numbers inside these are identifiers, never spec values: DO-204AL must never
    yield '-204 A', and LM317A must never yield '317 A'.
    """
    spans = [(m.start(), m.end()) for m in PART_RE.finditer(text)]
    spans += [(m.start(), m.end()) for m in PACKAGE_RE.finditer(text)]
    return spans


def _masked(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def contamination(window: str, mpn: str, known_parts: set[str]) -> str:
    """Return a foreign catalog part named in this window, or ''.

    Only parts we actually know to be distinct products count. An orderable
    suffix like LM317LIDR is the same product in different packaging and must
    not trip the guard, while a genuine sibling like LM317 in the LM317L
    datasheet must.
    """
    target = mpn.upper()
    for tok in sorted(part_tokens(window)):
        if tok != target and tok in known_parts:
            return tok
    return ""


# --------------------------------------------------------------------------
def find_series_headers(text: str, siblings: tuple, max_gap: int = 80) -> list[tuple[int, int, list[str]]]:
    """Every place the sibling part numbers appear consecutively.

    Vishay prints the part list as a page banner as well as a table header, so
    this deliberately returns ALL matches; the caller picks the one whose value
    row is actually shaped like a table.
    """
    sibs = list(siblings)
    if len(sibs) < 2:
        return []
    up = text.upper()
    first = sibs[0].upper()
    out, cursor = [], 0
    while True:
        i = up.find(first, cursor)
        if i < 0:
            break
        cursor = i + 1
        pos, end, ok = {sibs[0]: i}, i + len(first), True
        for s in sibs[1:]:
            j = up.find(s.upper(), end)
            if j < 0 or (j - end) > max_gap:
                ok = False
                break
            pos[s] = j
            end = j + len(s)
        if ok:
            out.append((i, end, [k for k, _ in sorted(pos.items(), key=lambda kv: kv[1])]))
    return out


def _anchor_all(region: str, spec: AttributeSpec) -> list[tuple[int, int, str]]:
    """Every alias hit, symbol-like aliases first (most reliable inside tables)."""
    hits = []
    for a in sorted(spec.aliases, key=lambda x: (" " in x, -len(x))):
        for m in re.finditer(re.escape(a), region, re.IGNORECASE):
            hits.append((m.start(), m.end(), a))
        if hits:
            break
    return hits[:8]


def _row_numbers(region: str, start: int, n_expected: int):
    """Walk tokens after an anchor, collecting one table row's numeric cells."""
    nums: list[float] = []
    cells: list[tuple[int, int]] = []
    unit, skipped = "", 0
    for k, m in enumerate(TOKEN_RE.finditer(region, start)):
        if k > 70:
            break
        t = m.group(0).strip().strip("|")
        if not t:
            continue
        if UNIT_ONLY_RE.match(t):
            if nums:
                unit = t
                break
            continue
        if PURE_NUMBER_RE.match(t.replace("−", "-")):
            nums.append(float(t.replace("−", "-").replace(",", ".")))
            cells.append((m.start(), m.end()))
            if len(nums) >= n_expected:
                nxt = TOKEN_RE.search(region, m.end())
                if nxt and UNIT_ONLY_RE.match(nxt.group(0)):
                    unit = nxt.group(0)
                break
            continue
        if nums:
            break
        skipped += 1
        if skipped > 5:
            break
    return nums, cells, unit


class TableColumnExtractor:
    name = "table_column"

    def extract(self, doc: IngestedDoc, mpn: str, spec: AttributeSpec,
                siblings: tuple, known_parts: set[str]) -> list[Candidate]:
        if spec.dtype != "number" or len(siblings) < 2:
            return []
        found: dict[str, tuple[int, Candidate]] = {}   # value -> (score, candidate)
        for hs, he, ordered in find_series_headers(doc.text, siblings):
            names = [n.upper() for n in ordered]
            if mpn.upper() not in names:
                continue
            col, n = names.index(mpn.upper()), len(names)
            region = doc.text[he: he + 9000]
            for a_start, a_end, alias in _anchor_all(region, spec):
                nums, cells, unit = _row_numbers(region, a_end, n)
                if not nums:
                    continue
                if len(nums) >= n:
                    value, col_idx, score = nums[col], col, 3.0
                    fs, fe = cells[col]
                elif len(nums) == 1:
                    # One value for the whole series. Legitimate (IF(AV) really is
                    # 1.0 A for every 1N400x) but much weaker than a resolved
                    # column, because a summary banner looks identical.
                    value, col_idx, score = nums[0], -1, 1.2
                    fs, fe = cells[0]
                else:
                    continue                                  # ambiguous -> abstain
                if not spec.in_plausible_range(float(value)):
                    continue
                vkey = f"{value:g}"
                if vkey in found and found[vkey][0] >= score:
                    continue
                q_start = he + a_start
                q_end = he + max(fe, a_end)
                ev = EvidenceSpan(doc.doc.doc_id, doc.page_for_offset(q_start),
                                  q_start, q_end, doc.text[q_start:q_end])
                ev.focus_start, ev.focus_end = he + fs, he + fe
                ev.focus_text = doc.text[he + fs: he + fe]
                c = Candidate(spec.name, str(value), ev, self.name,
                              doc.doc.doc_id, doc.doc.authority)
                c.column_index, c.column_of, c.unit = col_idx, n, unit
                c.precision = score
                normalize_candidate(c, spec, unit)
                found[vkey] = (score, c)
        if not found:
            return []
        # Rank by evidence quality but return them all: two defensible values for
        # one attribute is a conflict a human must see, not a tie to break silently.
        ranked = sorted(found.values(), key=lambda sc: -sc[0])
        return [c for _, c in ranked[:3]]


class RangeExtractor:
    name = "range"

    def extract(self, doc: IngestedDoc, mpn: str, spec: AttributeSpec,
                siblings: tuple, known_parts: set[str]) -> list[Candidate]:
        if not spec.is_range_end:
            return []
        want_min = spec.is_range_end == "min"
        text = doc.text
        scored = []
        for alias in sorted(spec.aliases, key=len, reverse=True):
            for m in re.finditer(re.escape(alias), text, re.IGNORECASE):
                window = text[m.end(): m.end() + 140]
                rm = RANGE_RE.search(window)
                if not rm:
                    continue
                # The DOCUMENT must state the unit. Never fall back to the schema's
                # unit: two bare numbers near the word "temperature" are not a
                # temperature range, and assuming so publishes fiction.
                unit = rm.group(4) or rm.group(2) or ""
                if not unit or not compatible(unit, spec.unit or ""):
                    continue
                lo, hi = float(rm.group(1).replace("−", "-")), float(rm.group(3).replace("−", "-"))
                if lo >= hi:
                    continue
                lo_s = spec.in_plausible_range(lo) if want_min else True
                hi_s = spec.in_plausible_range(hi) if not want_min else True
                raw = rm.group(1) if want_min else rm.group(3)
                if not spec.in_plausible_range(float(raw.replace("−", "-"))):
                    continue
                # prefer explicit both-ended units and a wide, realistic span
                score = (2 if (rm.group(2) and rm.group(4)) else 1) + (1 if (hi - lo) >= 50 else 0)
                g0 = rm.start(1) if want_min else rm.start(3)
                g1 = rm.end(1) if want_min else rm.end(3)
                q_start = m.start()
                q_end = m.end() + rm.end()
                ev = EvidenceSpan(doc.doc.doc_id, doc.page_for_offset(q_start),
                                  q_start, q_end, text[q_start:q_end])
                ev.focus_start, ev.focus_end = m.end() + g0, m.end() + g1
                ev.focus_text = text[ev.focus_start:ev.focus_end]
                c = Candidate(spec.name, raw, ev, self.name, doc.doc.doc_id,
                              doc.doc.authority)
                c.unit = unit
                c.precision = 1.0 + 0.5 * score
                c.contaminated_by = contamination(
                    text[max(0, m.start() - 90): q_end + 60], mpn, known_parts)
                scored.append((score, normalize_candidate(c, spec, unit)))
            if scored:
                break
        if not scored:
            return []
        scored.sort(key=lambda sc: -sc[0])
        return [scored[0][1]]


class InlineSpecExtractor:
    name = "inline_spec"

    def extract(self, doc: IngestedDoc, mpn: str, spec: AttributeSpec,
                siblings: tuple, known_parts: set[str]) -> list[Candidate]:
        text = doc.text
        out, seen = [], set()
        for alias in sorted(spec.aliases, key=len, reverse=True):
            for m in re.finditer(re.escape(alias), text, re.IGNORECASE):
                a_end = m.end()
                window = text[a_end: a_end + 130]
                hit = self._value_in(window, spec)
                if not hit:
                    continue
                raw, unit, v0, v1 = hit
                q_start, q_end = m.start(), a_end + v1
                key = collapse(text[q_start:q_end]).lower()
                if key in seen:
                    continue
                seen.add(key)
                ev = EvidenceSpan(doc.doc.doc_id, doc.page_for_offset(q_start),
                                  q_start, q_end, text[q_start:q_end])
                ev.focus_start, ev.focus_end = a_end + v0, a_end + v1
                ev.focus_text = text[ev.focus_start:ev.focus_end]
                c = Candidate(spec.name, raw, ev, self.name, doc.doc.doc_id,
                              doc.doc.authority)
                c.unit = unit
                c.precision = 1.0
                c.contaminated_by = contamination(
                    text[max(0, q_start - 90): q_end + 90], mpn, known_parts)
                normalize_candidate(c, spec, unit)
                if c.value is not None and spec.dtype == "number" \
                        and not spec.in_plausible_range(float(c.value)):
                    c.normalize_error = f"{c.display} outside plausible range"
                out.append(c)
                if len(out) >= 8:
                    return out
        return out

    def _value_in(self, window: str, spec: AttributeSpec):
        masks = masked_spans(window)
        if spec.dtype == "number":
            for nm in NUMBER_RE.finditer(window.replace("−", "-")):
                if _masked(nm.start(), masks) or _masked(nm.end() - 1, masks):
                    continue                      # number lives inside an identifier
                tail = window[nm.end(): nm.end() + 12]
                um = re.match(r"\s*(°\s?[CF]|[munpkKM]?(?:A|V|W|F|s|Hz)|Ω|ohm|dB|%|℃)",
                              tail, re.IGNORECASE)
                unit = um.group(1) if um else ""
                if um:
                    after = tail[um.end(): um.end() + 1]
                    if after.isalpha():
                        continue                  # "204AL" -- not a unit
                if spec.unit and (not unit or not compatible(unit, spec.unit)):
                    continue                      # dimensional guard
                end = nm.end() + (um.end() if um else 0)
                return window[nm.start():end], unit, nm.start(), end
            return None
        if spec.dtype == "string" and spec.name == "package":
            pm = PACKAGE_RE.search(window)
            return (pm.group(0), "", pm.start(), pm.end()) if pm else None
        if spec.dtype == "enum":
            for v in spec.enum:
                em = re.search(v.replace("_", "[ -]?"), window, re.IGNORECASE)
                if em:
                    return em.group(0), "", em.start(), em.end()
            return None
        if spec.dtype == "bool":
            bm = re.search(r"(compliant|lead[- ]free|Pb[- ]free|halogen[- ]free)",
                           window, re.IGNORECASE)
            return (bm.group(0), "", bm.start(), bm.end()) if bm else None
        return None


DETERMINISTIC = [TableColumnExtractor(), RangeExtractor(), InlineSpecExtractor()]


def extract_from_doc(doc: IngestedDoc, mpn: str, pclass: ProductClass,
                     siblings: tuple = (), known_parts: set[str] | None = None,
                     extractors=None) -> list[Candidate]:
    """Run every strategy per attribute, verifying as we go.

    Verification happens INSIDE this loop so the early-exit can trust it: a clean,
    verified hit from a high-precision strategy stops us falling through to noisier
    ones, which is what keeps inline_spec from re-litigating a solved table cell.
    """
    from .verify import verify_candidate          # local import avoids a cycle

    known_parts = known_parts or set()
    sm = doc.sections
    out: list[Candidate] = []
    for spec in pclass.attributes:
        for ex in (extractors or DETERMINISTIC):
            got = ex.extract(doc, mpn, spec, siblings, known_parts)
            if not got:
                continue
            kept = []
            for c in got:
                verify_candidate(doc, c)
                if c.evidence:
                    off = (c.evidence.focus_start if c.evidence.focus_start >= 0
                           else c.evidence.char_start)
                    c.section = sm.at(off)
                    c.from_graph = sm.is_graph_region(off)
                # Chart tick values are never ratings, and an attribute only
                # accepts claims from sections with authority over it.
                if c.from_graph:
                    c.dropped_reason = "evidence sits in a characteristic-curve plot"
                elif spec.allow_sections and c.section not in spec.allow_sections:
                    c.dropped_reason = (
                        f"section {c.section} has no authority over {spec.name} "
                        f"(expected {'/'.join(spec.allow_sections)})")
                else:
                    kept.append(c)
            out.extend(got)
            if any(c.usable and not c.contaminated_by and not c.dropped_reason
                   for c in kept):
                break
    return out
