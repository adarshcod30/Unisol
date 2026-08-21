"""Commerce-ready output.

Two shapes from one record:
  * a flat attribute payload a PIM can ingest
  * schema.org/Product JSON-LD, which is what makes a product page legible to
    search engines and to the AI assistants that increasingly sit in front of
    them -- the channel a distributor cannot afford to be invisible in.

Only AUTO_PUBLISH attributes are emitted. Anything in review is withheld by
construction rather than by convention: unreviewed data cannot leak into a feed
because this function never sees it as publishable.
"""
from __future__ import annotations

from .models import ProductRecord, DECISION_AUTO
from . import schema as schema_mod


def commerce_payload(rec: ProductRecord) -> dict:
    pclass = schema_mod.get(rec.product_class) if rec.product_class else None
    attrs = {}
    for name, a in rec.attributes.items():
        if a.decision != DECISION_AUTO:
            continue
        spec = pclass.by_name(name) if pclass else None
        attrs[name] = {
            "label": spec.label if spec else name,
            "value": a.value, "unit": a.unit, "display": a.display,
            "confidence": a.confidence,
            "safety_critical": a.safety_critical,
            "evidence": {
                "document": a.evidence.doc_id, "page": a.evidence.page,
                "quote": a.evidence.quote.strip()[:300],
                "match": a.evidence.match_mode,
            } if a.evidence else None,
        }
    return {
        "sku": rec.sku, "mpn": rec.mpn, "brand": rec.brand,
        "product_class": rec.product_class,
        "published_attributes": attrs,
        "withheld_for_review": sorted(
            n for n, a in rec.attributes.items() if a.decision != DECISION_AUTO),
        "sources": [{"doc_id": d.doc_id, "publisher": d.publisher, "url": d.url,
                     "sha256": d.sha256[:16]} for d in rec.docs],
    }


def json_ld(rec: ProductRecord) -> dict:
    props = []
    for name, a in rec.attributes.items():
        if a.decision != DECISION_AUTO:
            continue
        p = {"@type": "PropertyValue", "name": name, "value": a.value}
        if a.unit:
            p["unitText"] = a.unit
        props.append(p)
    return {
        "@context": "https://schema.org",
        "@type": "Product",
        "sku": rec.sku,
        "mpn": rec.mpn,
        "name": f"{rec.brand} {rec.mpn}",
        "brand": {"@type": "Brand", "name": rec.brand},
        "description": rec.input_description,
        "additionalProperty": props,
    }


def ai_readiness(rec: ProductRecord) -> dict:
    """How answerable is this product to an AI shopping assistant?

    An assistant can only recommend a part whose specs are present, unambiguous
    and machine-readable. Missing attributes and withheld conflicts are exactly
    the gaps that make a distributor invisible to that channel.
    """
    pclass = schema_mod.get(rec.product_class)
    total = len(pclass.attributes)
    pub = sum(1 for a in rec.attributes.values() if a.decision == DECISION_AUTO)
    withheld = sum(1 for a in rec.attributes.values() if a.decision != DECISION_AUTO)
    traced = sum(1 for a in rec.attributes.values()
                 if a.decision == DECISION_AUTO and a.evidence and a.evidence.verified)
    score = round(100 * (0.6 * pub / total + 0.4 * (traced / pub if pub else 0)), 1)
    return {
        "score": score,
        "attributes_expected": total,
        "attributes_published": pub,
        "attributes_withheld": withheld,
        "provenance_coverage": round(traced / pub, 4) if pub else 0.0,
        "gaps": sorted(set(pclass.names) - set(
            n for n, a in rec.attributes.items() if a.decision == DECISION_AUTO)),
    }
