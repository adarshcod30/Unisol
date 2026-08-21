"""Multi-source arbitration.

Candidates arrive from several documents and several strategies, often
disagreeing. This module clusters them by value and picks a winner using evidence
quality rather than raw count.

Two anti-patterns it deliberately avoids:

  Ballot stuffing -- a regex that matches the same value eight times in one PDF is
  not eight independent confirmations. Votes are weighted per (document, value)
  pair, with within-document repetition contributing only sub-linearly.

  Treating all sources alike -- a manufacturer datasheet outranks a marketplace
  listing. Authority tier scales the vote.

Cross-document disagreement is escalated, never silently resolved: two
manufacturers publishing different numbers for the same part is exactly the case
a human must see.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .models import Candidate, ResolvedAttribute
from .schema import AttributeSpec


def _value_key(v) -> str:
    if isinstance(v, float):
        return f"{round(v, 6):g}"
    return str(v)


def authority_weight(tier: int) -> float:
    return {1: 1.0, 2: 0.6, 3: 0.4, 4: 0.25}.get(tier, 0.15)


def arbitrate(attribute: str, cands: list[Candidate], spec: AttributeSpec) -> ResolvedAttribute:
    res = ResolvedAttribute(attribute=attribute, safety_critical=spec.safety_critical)
    res.candidates = cands

    live = [c for c in cands if c.usable and not c.contaminated_by and not c.dropped_reason]
    section_dropped = [c for c in cands if c.dropped_reason]
    if section_dropped:
        res.reasons.append(
            f"{len(section_dropped)} candidate(s) rejected on provenance: "
            + "; ".join(sorted({c.dropped_reason for c in section_dropped}))[:160])
    quarantined = [c for c in cands if c.contaminated_by]
    if quarantined:
        res.reasons.append(
            f"{len(quarantined)} candidate(s) quarantined: evidence window named "
            + ", ".join(sorted({c.contaminated_by for c in quarantined})))
    res.total_sources = len({c.doc_id for c in cands})

    if not live:
        res.reasons.append("no candidate survived the evidence gate")
        return res

    # (doc, value) -> best precision, repetition count, representative candidate
    cells: dict[tuple[str, str], dict] = {}
    for c in live:
        k = (c.doc_id, _value_key(c.value))
        prec = float(c.precision)
        cell = cells.setdefault(k, {"prec": 0, "n": 0, "rep": c, "tier": c.tier})
        cell["n"] += 1
        if prec > cell["prec"]:
            cell["prec"], cell["rep"] = prec, c

    clusters: dict[str, dict] = defaultdict(
        lambda: {"score": 0.0, "docs": set(), "best": None, "best_prec": -1, "reps": 0})
    for (doc_id, vkey), cell in cells.items():
        # repetition helps break ties but must never outweigh a better strategy
        weight = cell["prec"] * authority_weight(cell["tier"]) * (1 + 0.5 * math.log2(cell["n"] + 1))
        cl = clusters[vkey]
        cl["score"] += weight
        cl["docs"].add(doc_id)
        cl["reps"] += cell["n"]
        if cell["prec"] > cl["best_prec"]:
            cl["best_prec"], cl["best"] = cell["prec"], cell["rep"]

    ranked = sorted(clusters.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    top_key, top = ranked[0]
    win: Candidate = top["best"]

    res.value, res.unit, res.display = win.value, win.unit, win.display
    res.evidence = win.evidence
    res.agreeing_sources = len(top["docs"])

    losers = ranked[1:]
    cross_doc = [k for k, c in losers if c["docs"] - top["docs"]]
    res.conflict_values = [
        (c["best"].display or k) for k, c in losers[:4]
    ]
    res.conflicting = bool(cross_doc)
    if res.conflicting:
        res.reasons.append(
            f"cross-document conflict: {len(losers)+1} distinct values across "
            f"{res.total_sources} sources")
    elif losers:
        res.reasons.append(
            f"{len(losers)} competing value(s) within a single document, resolved "
            f"by evidence quality ({win.extractor})")
    if res.agreeing_sources > 1:
        res.reasons.append(f"{res.agreeing_sources} independent sources agree")
    if win.section and win.section != "OTHER":
        res.reasons.append(f"evidence read from the {win.section} section")
    if win.column_index >= 0:
        res.reasons.append(
            f"read from column {win.column_index + 1} of {win.column_of} in a "
            f"series ratings table")
    return res


def arbitrate_all(cands: list[Candidate], pclass) -> dict[str, ResolvedAttribute]:
    by_attr: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        by_attr[c.attribute].append(c)
    out = {}
    for spec in pclass.attributes:
        got = by_attr.get(spec.name, [])
        if got:
            out[spec.name] = arbitrate(spec.name, got, spec)
    return out
