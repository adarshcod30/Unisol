"""The Bedrock path, proven without credentials.

Response parsing, prompt construction, self-consistency and -- most importantly --
the evidence gate applied to LLM output are all pure logic. They are tested here
against recorded/simulated Bedrock responses, so the only unverified part of the
integration is the network call itself.

The test that matters: a model that returns a fluent, correctly-typed, perfectly
schema-valid value with an INVENTED citation must contribute nothing.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from specledger.ingest import ingest
from specledger.llm import LLMExtractor, TOOL_NAME, parse_bedrock, SYSTEM
from specledger.models import MATCH_NOT_FOUND
from specledger.schema import RECTIFIER_DIODE
from specledger.verify import verify_candidate


def bedrock_response(**payload):
    """The exact shape bedrock-runtime.converse returns for a forced tool call."""
    return {"output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "tu_1", "name": TOOL_NAME, "input": payload}}]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 900, "outputTokens": 80}}


class FakeBackend:
    """Stands in for Bedrock. Returns scripted payloads, one per sample."""
    name = "fake"

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts = []
        self.temperatures = []

    def call(self, prompt, temperature):
        self.prompts.append(prompt)
        self.temperatures.append(temperature)
        p = self.payloads[min(len(self.prompts) - 1, len(self.payloads) - 1)]
        return parse_bedrock(bedrock_response(**p))


@pytest.fixture(scope="module")
def doc():
    return ingest("vishay-1n4001-4007")


@pytest.fixture
def spec():
    return RECTIFIER_DIODE.by_name("reverse_voltage_max")


def test_parse_bedrock_extracts_tool_payload():
    r = bedrock_response(found=True, value="50 V", quote="VRRM\n50")
    assert parse_bedrock(r) == {"found": True, "value": "50 V", "quote": "VRRM\n50"}


def test_parse_bedrock_tolerates_a_text_only_response():
    assert parse_bedrock({"output": {"message": {"content": [{"text": "hello"}]}}}) is None
    assert parse_bedrock({}) is None


def test_real_quote_survives_the_gate(doc, spec, monkeypatch):
    monkeypatch.setattr("specledger.llm.available", lambda: True)
    quote = doc.text[doc.text.find("VRRM"):doc.text.find("VRRM") + 12]
    ex = LLMExtractor(backend=FakeBackend([dict(found=True, value="50 V", quote=quote)]),
                      max_windows=1, samples=1)
    cands = ex.extract(doc, "1N4001", spec, (), set())
    assert cands
    verify_candidate(doc, cands[0])
    assert cands[0].evidence.verified
    assert cands[0].value == 50.0


def test_fabricated_citation_from_the_model_is_discarded(doc, spec, monkeypatch):
    """The core safety property, applied to LLM output."""
    monkeypatch.setattr("specledger.llm.available", lambda: True)
    ex = LLMExtractor(backend=FakeBackend([dict(
        found=True, value="1200 V",
        quote="Maximum repetitive peak reverse voltage VRRM 1200 V per IEC 60747-2")]),
        max_windows=1, samples=1)
    cands = ex.extract(doc, "1N4001", spec, (), set())
    assert cands, "the candidate is produced ..."
    verify_candidate(doc, cands[0])
    assert cands[0].evidence.match_mode == MATCH_NOT_FOUND
    assert not cands[0].usable, "... but must never be publishable"


def test_abstention_is_respected(doc, spec, monkeypatch):
    monkeypatch.setattr("specledger.llm.available", lambda: True)
    ex = LLMExtractor(backend=FakeBackend([dict(found=False)]), max_windows=1, samples=1)
    assert ex.extract(doc, "1N4001", spec, (), set()) == []


def test_self_consistency_is_measured_across_samples(doc, spec, monkeypatch):
    """Two of three samples agree -> consistency 2/3, and the majority wins."""
    monkeypatch.setattr("specledger.llm.available", lambda: True)
    q = doc.text[doc.text.find("VRRM"):doc.text.find("VRRM") + 12]
    ex = LLMExtractor(backend=FakeBackend([
        dict(found=True, value="50 V", quote=q),
        dict(found=True, value="1000 V", quote=q),
        dict(found=True, value="50 V", quote=q),
    ]), max_windows=1, samples=3)
    c = ex.extract(doc, "1N4001", spec, (), set())[0]
    assert c.samples == 3
    assert c.self_consistency == pytest.approx(2 / 3, abs=0.01)
    assert c.value == 50.0, "the majority value wins"


def test_disagreement_lowers_evidence_strength(doc, spec, monkeypatch):
    monkeypatch.setattr("specledger.llm.available", lambda: True)
    q = doc.text[doc.text.find("VRRM"):doc.text.find("VRRM") + 12]
    agree = LLMExtractor(backend=FakeBackend([dict(found=True, value="50 V", quote=q)] * 3),
                         max_windows=1, samples=3).extract(doc, "1N4001", spec, (), set())[0]
    split = LLMExtractor(backend=FakeBackend([
        dict(found=True, value="50 V", quote=q),
        dict(found=True, value="1000 V", quote=q),
        dict(found=True, value="800 V", quote=q)]),
        max_windows=1, samples=3).extract(doc, "1N4001", spec, (), set())[0]
    assert agree.precision > split.precision


def test_llm_never_outranks_a_resolved_series_column(doc, spec, monkeypatch):
    """An LLM at full agreement must still lose to a deterministic column read."""
    from specledger.extract import TableColumnExtractor
    from specledger import corpus
    monkeypatch.setattr("specledger.llm.available", lambda: True)
    q = doc.text[doc.text.find("VRRM"):doc.text.find("VRRM") + 12]
    llm_c = LLMExtractor(backend=FakeBackend([dict(found=True, value="1000 V", quote=q)] * 3),
                         max_windows=1, samples=3).extract(doc, "1N4001", spec, (), set())[0]
    table_c = TableColumnExtractor().extract(
        doc, "1N4001", spec, corpus.BY_ID["vishay-1n4001-4007"].covers, set())[0]
    assert table_c.precision > llm_c.precision


def test_prompt_carries_the_sibling_warning(doc, spec, monkeypatch):
    monkeypatch.setattr("specledger.llm.available", lambda: True)
    be = FakeBackend([dict(found=False)])
    LLMExtractor(backend=be, max_windows=1, samples=1).extract(
        doc, "1N4001", spec, ("1N4001", "1N4007"), set())
    assert "1N4007" in be.prompts[0]
    assert "Part number: 1N4001" in be.prompts[0]
    assert "column for the requested part only" in SYSTEM


def test_pipeline_tests_never_call_the_real_network(monkeypatch):
    """Guards the fix itself: even with credentials configured, enrich_all()
    must not touch the LLM panel unless the caller opts in explicitly. A test
    suite whose speed and cost depend on ambient .env contents is not hermetic."""
    from specledger.catalog import CATALOG
    from specledger.pipeline import enrich_all

    def _boom(*a, **kw):
        raise AssertionError("a hermetic test path invoked the network")

    monkeypatch.setattr("specledger.llm.available", lambda: True)   # simulate creds present
    monkeypatch.setattr("specledger.llm.LLMExtractor.extract", _boom)
    from specledger.extract import DETERMINISTIC
    recs = enrich_all(skus=CATALOG[:1], extractors=DETERMINISTIC)
    assert recs and recs[0].attributes, "deterministic-only extraction still works"
