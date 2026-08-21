"""The anti-hallucination gate.

Every candidate value carries a verbatim quote plus character offsets. This module
checks that claim against the actual document bytes. It is pure string comparison --
no model is involved in deciding whether a model told the truth.

Outcome ladder:
  exact      text[start:end] == quote                      -> full trust
  normalized equal after whitespace collapse at offsets    -> full trust (PDF spacing)
  relocated  quote is in the doc, but not at those offsets -> value real, offsets repaired
  not_found  quote appears nowhere in the doc              -> FABRICATED, candidate dropped
"""
from __future__ import annotations

from .ingest import IngestedDoc, collapse
from .models import (Candidate, EvidenceSpan, MATCH_EXACT, MATCH_NORMALIZED,
                     MATCH_RELOCATED, MATCH_NOT_FOUND)


def verify_span(doc: IngestedDoc, span: EvidenceSpan) -> EvidenceSpan:
    quote = (span.quote or "").strip()
    if not quote:
        span.match_mode = MATCH_NOT_FOUND
        span.verified = False
        return span

    at = doc.slice(span.char_start, span.char_end)
    if at == quote:
        span.match_mode = MATCH_EXACT
    elif collapse(at) == collapse(quote) and collapse(quote):
        span.match_mode = MATCH_NORMALIZED
    else:
        found = doc.find_quote(quote)
        if found >= 0:
            span.char_start = found
            span.char_end = found + len(quote)
            span.match_mode = MATCH_RELOCATED
        else:
            span.match_mode = MATCH_NOT_FOUND

    span.verified = span.match_mode != MATCH_NOT_FOUND
    if span.verified:
        span.page = doc.page_for_offset(span.char_start)
    return span


def verify_candidate(doc: IngestedDoc, cand: Candidate) -> Candidate:
    if cand.evidence is None:
        return cand
    cand.evidence = verify_span(doc, cand.evidence)
    return cand


def verify_all(docs: dict[str, IngestedDoc], cands: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
    """Returns (kept, dropped). Dropped candidates failed the evidence gate."""
    kept, dropped = [], []
    for c in cands:
        d = docs.get(c.doc_id)
        if d is None:
            dropped.append(c)
            continue
        verify_candidate(d, c)
        (kept if (c.evidence and c.evidence.verified) else dropped).append(c)
    return kept, dropped
