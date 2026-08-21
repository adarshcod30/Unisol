"""The central claim: a value whose quote is not in the source cannot be published.

This is the test that matters most. The deterministic extractors quote real text
by construction, so to exercise the gate properly we simulate a FABRICATING
extractor -- exactly the failure mode an LLM exhibits, where a confident,
correctly-typed, schema-valid value arrives with a quote that was never written.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from specledger.ingest import ingest
from specledger.models import (Candidate, EvidenceSpan, MATCH_EXACT,
                               MATCH_NOT_FOUND, MATCH_RELOCATED)
from specledger.schema import RECTIFIER_DIODE
from specledger.verify import verify_candidate, verify_all


@pytest.fixture(scope="module")
def doc():
    return ingest("vishay-1n4001-4007")


def _cand(doc, quote, start=0, end=None):
    ev = EvidenceSpan(doc.doc.doc_id, 1, start, end if end is not None else start + len(quote), quote)
    return Candidate("reverse_voltage_max", "50", ev, "test", doc.doc.doc_id, "MFR_DATASHEET")


def test_real_quote_at_correct_offsets_is_exact(doc):
    idx = doc.text.find("VRRM")
    c = _cand(doc, doc.text[idx:idx + 20], idx, idx + 20)
    verify_candidate(doc, c)
    assert c.evidence.match_mode == MATCH_EXACT
    assert c.evidence.verified


def test_real_quote_at_wrong_offsets_is_relocated_not_trusted_blindly(doc):
    idx = doc.text.find("VRRM")
    quote = doc.text[idx:idx + 20]
    c = _cand(doc, quote, start=5, end=25)          # offsets deliberately wrong
    verify_candidate(doc, c)
    assert c.evidence.match_mode == MATCH_RELOCATED
    assert c.evidence.char_start == idx             # offsets repaired to the truth


def test_fabricated_quote_is_rejected(doc):
    """A plausible sentence that was never written must not survive."""
    c = _cand(doc, "Maximum repetitive peak reverse voltage VRRM 1200 V per IEC 60747")
    verify_candidate(doc, c)
    assert c.evidence.match_mode == MATCH_NOT_FOUND
    assert not c.evidence.verified
    assert not c.usable, "a fabricated quote must never be usable"


def test_fabricating_extractor_contributes_nothing(doc):
    """Simulate an LLM that invents every citation. Nothing reaches the catalog."""
    fabricated = [
        _cand(doc, "The 1N4001 is rated 1200 V continuous."),
        _cand(doc, "Absolute maximum reverse voltage: 1200 volts."),
        _cand(doc, "This device withstands 5 kA of surge current."),
    ]
    kept, dropped = verify_all({doc.doc.doc_id: doc}, fabricated)
    assert kept == []
    assert len(dropped) == 3


def test_empty_quote_is_not_evidence(doc):
    c = _cand(doc, "")
    verify_candidate(doc, c)
    assert not c.evidence.verified


def test_whitespace_differences_are_tolerated(doc):
    """PDF extraction mangles whitespace; that must not look like fabrication."""
    idx = doc.text.find("VRRM")
    quote = doc.text[idx:idx + 24].replace("\n", "  ")
    c = _cand(doc, quote, idx, idx + 24)
    verify_candidate(doc, c)
    assert c.evidence.verified
