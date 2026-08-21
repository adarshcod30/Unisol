"""End-to-end invariants that must hold for every record."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from specledger.catalog import CATALOG
from specledger.confidence import ConfidenceModel
from specledger.models import DECISION_AUTO
from specledger.extract import DETERMINISTIC
from specledger.pipeline import enrich_all
from specledger.publish import commerce_payload, json_ld


@pytest.fixture(scope="module")
def records():
    # Pinned to DETERMINISTIC: this suite must stay fast, free and hermetic
    # even when real LLM credentials are sitting in the environment. The live
    # LLM path is exercised deliberately in eval/run_eval.py, not here.
    return enrich_all(model=ConfidenceModel.load(), extractors=DETERMINISTIC)


def test_every_sku_produces_a_record(records):
    assert len(records) == len(CATALOG)


def test_no_published_attribute_lacks_verified_evidence(records):
    """The invariant the whole system exists to enforce."""
    for r in records:
        for name, a in r.attributes.items():
            if a.decision == DECISION_AUTO:
                assert a.evidence is not None, f"{r.sku}.{name}"
                assert a.evidence.verified, f"{r.sku}.{name} published unverified"
                assert a.evidence.quote.strip(), f"{r.sku}.{name} has an empty quote"


def test_conflicting_attributes_are_never_auto_published(records):
    for r in records:
        for name, a in r.attributes.items():
            if a.conflicting:
                assert a.decision != DECISION_AUTO, f"{r.sku}.{name} published despite conflict"


def test_error_level_rule_violations_are_never_auto_published(records):
    for r in records:
        for name, a in r.attributes.items():
            if any(v.startswith("[error]") for v in a.rule_violations):
                assert a.decision != DECISION_AUTO, f"{r.sku}.{name}"


def test_published_payload_excludes_everything_in_review(records):
    for r in records:
        payload = commerce_payload(r)
        for name in payload["published_attributes"]:
            assert r.attributes[name].decision == DECISION_AUTO
        assert set(payload["withheld_for_review"]).isdisjoint(payload["published_attributes"])


def test_json_ld_is_wellformed(records):
    d = json_ld(records[0])
    assert d["@type"] == "Product" and d["@context"] == "https://schema.org"
    assert all(p["@type"] == "PropertyValue" for p in d["additionalProperty"])


def test_every_attribute_can_explain_itself(records):
    for r in records:
        for name, a in r.attributes.items():
            assert a.reasons, f"{r.sku}.{name} has no explanation"
