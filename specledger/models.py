"""Core data model for SpecLedger.

The invariant this whole system is built on: no attribute value exists without an
EvidenceSpan, and no EvidenceSpan is trusted until verify.py has matched its quote
against the actual source document bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import json

# Source authority. Lower number wins arbitration ties.
AUTHORITY = {
    "MFR_DATASHEET": 1,
    "MFR_WEB": 2,
    "AUTHORIZED_DIST": 3,
    "MARKETPLACE": 4,
    "UNKNOWN": 9,
}

# How well an evidence quote matched the source document.
MATCH_EXACT = "exact"            # text[start:end] == quote
MATCH_NORMALIZED = "normalized"  # equal after whitespace collapse at those offsets
MATCH_RELOCATED = "relocated"    # quote is in the doc, but not at the claimed offsets
MATCH_NOT_FOUND = "not_found"    # quote is nowhere in the doc -> fabricated

DECISION_AUTO = "AUTO_PUBLISH"
DECISION_REVIEW = "REVIEW"
DECISION_REJECT = "REJECT"


@dataclass
class SourceDoc:
    doc_id: str
    url: str
    publisher: str
    authority: str          # key into AUTHORITY
    local_path: str
    sha256: str
    fetched_at: str = ""
    page_count: int = 0
    title: str = ""

    @property
    def tier(self) -> int:
        return AUTHORITY.get(self.authority, 9)


@dataclass
class EvidenceSpan:
    """A pointer into a source document. This is the chain of custody."""
    doc_id: str
    page: int
    char_start: int
    char_end: int
    quote: str
    match_mode: str = MATCH_NOT_FOUND
    verified: bool = False
    # Optional tighter span pinning the exact value token inside the quote.
    # For series tables the quote is the whole row (distinctive enough to verify)
    # while the focus pins the single cell the value was read from.
    focus_start: int = -1
    focus_end: int = -1
    focus_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    """One proposed value for one attribute, from one source. Pre-arbitration."""
    attribute: str
    raw_value: str
    evidence: Optional[EvidenceSpan] = None
    extractor: str = ""
    doc_id: str = ""
    authority: str = "UNKNOWN"
    # filled by normalize.py
    value: Any = None
    unit: Optional[str] = None
    display: str = ""
    normalize_error: str = ""
    # provenance of the extraction decision itself
    precision: float = 1.0        # evidence strength as judged by the extractor
                                  # ITSELF -- a full series-row read is far
                                  # stronger than a shared banner value, and a
                                  # static per-extractor constant loses that.
    column_index: int = -1        # which column of a series table, -1 if n/a
    column_of: int = -1           # how many columns that table had
    self_consistency: float = 1.0 # fraction of independent LLM samples that
                                  # produced this same value. Disagreement across
                                  # samples is real uncertainty, unlike a model's
                                  # self-reported confidence.
    samples: int = 1
    section: str = "OTHER"        # datasheet section the evidence came from
    from_graph: bool = False      # evidence sits among chart axis labels
    contaminated_by: str = ""     # a sibling MPN found in the evidence window
    dropped_reason: str = ""

    @property
    def tier(self) -> int:
        return AUTHORITY.get(self.authority, 9)

    @property
    def usable(self) -> bool:
        return (
            self.evidence is not None
            and self.evidence.verified
            and self.value is not None
            and not self.normalize_error
        )


@dataclass
class ResolvedAttribute:
    """Post-arbitration: one value, one decision, full provenance."""
    attribute: str
    value: Any = None
    unit: Optional[str] = None
    display: str = ""
    evidence: Optional[EvidenceSpan] = None
    candidates: list[Candidate] = field(default_factory=list)
    agreeing_sources: int = 0
    total_sources: int = 0
    conflicting: bool = False
    conflict_values: list[str] = field(default_factory=list)
    rule_violations: list[str] = field(default_factory=list)
    confidence: float = 0.0
    decision: str = DECISION_REJECT
    reasons: list[str] = field(default_factory=list)
    safety_critical: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["candidates"] = [asdict(c) for c in self.candidates]
        return d


@dataclass
class ProductRecord:
    sku: str
    mpn: str
    brand: str
    input_description: str
    product_class: str = ""
    attributes: dict[str, ResolvedAttribute] = field(default_factory=dict)
    docs: list[SourceDoc] = field(default_factory=list)
    created_at: str = ""
    pipeline_version: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "mpn": self.mpn,
            "brand": self.brand,
            "input_description": self.input_description,
            "product_class": self.product_class,
            "created_at": self.created_at,
            "pipeline_version": self.pipeline_version,
            "notes": self.notes,
            "docs": [asdict(d) for d in self.docs],
            "attributes": {k: v.to_dict() for k, v in self.attributes.items()},
        }

    def to_json(self, indent=2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def published(self) -> dict[str, ResolvedAttribute]:
        return {k: v for k, v in self.attributes.items() if v.decision == DECISION_AUTO}

    def queued(self) -> dict[str, ResolvedAttribute]:
        return {k: v for k, v in self.attributes.items() if v.decision == DECISION_REVIEW}
