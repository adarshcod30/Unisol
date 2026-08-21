"""End-to-end enrichment.

    sparse SKU -> sources -> candidates -> evidence gate -> arbitration
               -> physics rules -> calibrated confidence -> decision

Two modes run over identical inputs and identical source documents:

  "specledger"  the full pipeline
  "naive"       what a straightforward LLM-or-regex enrichment does: take the
                first plausible-looking match, trust it, publish it. No evidence
                gate, no column resolution, no contamination guard, no abstention.

The naive arm exists so the comparison in the eval is measured on the same corpus
rather than asserted from a slide.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import config, corpus, rules, schema
from .arbitrate import arbitrate_all
from .catalog import InputSKU, known_parts
from .confidence import ConfidenceModel
from .extract import (DETERMINISTIC, InlineSpecExtractor, extract_from_doc)
from .llm import panel
from .ingest import ingest
from .models import (Candidate, ProductRecord, ResolvedAttribute,
                     DECISION_AUTO, DECISION_REVIEW)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_brand(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def brand_matches(publisher: str, brand: str) -> bool:
    p, b = _norm_brand(publisher), _norm_brand(brand)
    if not p or not b:
        return False
    return p.startswith(b[:6]) or b.startswith(p[:6])


def _apply_brand_authority(cands, doc, sku) -> None:
    """A part number is not a product.

    Vishay's 1N4001 and Diodes Inc's 1N4001 are different physical parts that
    happen to share a JEDEC number, and they genuinely differ: Vishay rates VF at
    1.1 V, Diodes at 1.0 V, and their temperature ranges differ too. For a
    Vishay-branded SKU the Vishay datasheet is authoritative and the Diodes
    document is a cross-reference -- useful corroboration, but it must never
    outvote the manufacturer's own specification.
    """
    if brand_matches(doc.doc.publisher, sku.brand):
        return
    for c in cands:
        c.authority = "AUTHORIZED_DIST"


def enrich(sku: InputSKU, model: ConfidenceModel | None = None,
           mode: str = "specledger", extractors=None) -> ProductRecord:
    """extractors overrides the strategy panel. Defaults to panel(), which
    includes the LLM extractor whenever credentials are configured in the
    environment -- so callers that must stay hermetic (the test suite, notably)
    pass extractors=DETERMINISTIC explicitly rather than relying on ambient
    env state to decide whether they make live network calls."""
    model = model or ConfidenceModel.load()
    pclass = schema.get(sku.product_class)
    kp = known_parts()

    rec = ProductRecord(sku=sku.sku, mpn=sku.mpn, brand=sku.brand,
                        input_description=sku.description,
                        product_class=pclass.key, created_at=_now(),
                        pipeline_version=f"{config.PIPELINE_VERSION}:{mode}")

    doc_ids = corpus.docs_for_part(sku.mpn)
    if not doc_ids:
        rec.notes.append(f"no source document covers {sku.mpn}")
        return rec

    all_cands: list[Candidate] = []
    for did in doc_ids:
        d = ingest(did)
        rec.docs.append(d.doc)
        if mode == "naive":
            all_cands.extend(_naive_candidates(d, sku, pclass))
        else:
            got = extract_from_doc(
                d, sku.mpn, pclass, corpus.BY_ID[did].covers, kp,
                extractors if extractors is not None else panel())
            _apply_brand_authority(got, d, sku)
            if got and not brand_matches(d.doc.publisher, sku.brand):
                rec.notes.append(
                    f"{d.doc.publisher} datasheet used as cross-reference only "
                    f"({sku.brand} is the SKU brand)")
            all_cands.extend(got)

    if mode == "naive":
        for spec in pclass.attributes:
            first = next((c for c in all_cands if c.attribute == spec.name
                          and c.value is not None), None)
            if first is None:
                continue
            a = ResolvedAttribute(attribute=spec.name, value=first.value,
                                  unit=first.unit, display=first.display,
                                  evidence=first.evidence, candidates=[first],
                                  agreeing_sources=1, total_sources=len(doc_ids),
                                  safety_critical=spec.safety_critical)
            a.confidence, a.decision = 1.0, DECISION_AUTO      # trusts everything
            a.reasons.append("naive baseline: first match published unverified")
            rec.attributes[spec.name] = a
        return rec

    resolved = arbitrate_all(all_cands, pclass)

    values = {k: v.value for k, v in resolved.items()}
    viol = rules.evaluate(pclass.key, values)
    for k, msgs in rules.range_violations(pclass, values).items():
        viol.setdefault(k, []).extend(msgs)
    for name, msgs in viol.items():
        if name in resolved:
            resolved[name].rule_violations.extend(msgs)
            resolved[name].reasons.append(f"{len(msgs)} plausibility rule violation(s)")

    for a in resolved.values():
        model.apply(a)

    rec.attributes = resolved
    missing = [s.name for s in pclass.attributes if s.name not in resolved]
    if missing:
        rec.notes.append(f"no evidence found for: {', '.join(missing)}")
    return rec


def _naive_candidates(d, sku: InputSKU, pclass) -> list[Candidate]:
    """Inline matching with every guard switched off, plus blind trust."""
    ex = InlineSpecExtractor()
    out = []
    for spec in pclass.attributes:
        got = ex.extract(d, sku.mpn, spec, (), set())   # empty known_parts = no guard
        for c in got[:1]:
            c.contaminated_by = ""
            c.normalize_error = ""
            if c.evidence:
                c.evidence.verified = True               # never actually checked
            out.append(c)
    return out


def enrich_all(skus=None, model: ConfidenceModel | None = None,
               mode: str = "specledger", extractors=None) -> list[ProductRecord]:
    from .catalog import CATALOG
    model = model or ConfidenceModel.load()
    return [enrich(s, model, mode, extractors) for s in (skus or CATALOG)]


def summarize(records: list[ProductRecord]) -> dict:
    total = pub = rev = 0
    for r in records:
        for a in r.attributes.values():
            total += 1
            pub += a.decision == DECISION_AUTO
            rev += a.decision == DECISION_REVIEW
    return {"records": len(records), "attributes": total, "auto_published": pub,
            "queued_for_review": rev,
            "auto_publish_rate": round(pub / total, 4) if total else 0.0}
