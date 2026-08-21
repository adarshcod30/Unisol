"""SpecLedger API + Review Cockpit.

The endpoint that matters is /api/evidence/{sku}/{attribute}.png -- it renders the
actual source PDF page with the supporting sentence highlighted. Everything else
here is plumbing; that endpoint is the product. A reviewer should be able to go
from a published number to the sentence that justifies it in one click, without
leaving the tool and without trusting the tool.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Response          # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse      # noqa: E402
from pydantic import BaseModel                                # noqa: E402

from specledger import config, corpus, schema                 # noqa: E402
from specledger.catalog import CATALOG, BY_SKU                # noqa: E402
from specledger.confidence import ConfidenceModel             # noqa: E402
from specledger.ingest import ingest                          # noqa: E402
from specledger.models import DECISION_AUTO, DECISION_REVIEW  # noqa: E402
from specledger.pipeline import enrich_all                    # noqa: E402
from specledger.publish import ai_readiness, commerce_payload, json_ld  # noqa: E402
from specledger.store import (audit_trail, catalog_health, connect,     # noqa: E402
                              record_review, save_records)

app = FastAPI(title="SpecLedger", version=config.PIPELINE_VERSION)
WEB = Path(__file__).resolve().parent.parent / "web"
_STATE: dict = {}


def state():
    if "records" not in _STATE:
        model = ConfidenceModel.load()
        recs = enrich_all(model=model)
        save_records(recs)
        _STATE["records"] = {r.sku: r for r in recs}
        _STATE["model"] = model
    return _STATE


@app.get("/", response_class=HTMLResponse)
def cockpit():
    return (WEB / "index.html").read_text()


@app.get("/api/health")
def health():
    st = state()
    m = st["model"]
    return {
        "pipeline_version": config.PIPELINE_VERSION,
        "llm_extractor": "enabled" if config.LLM_AVAILABLE else "disabled (no API key)",
        "catalog": catalog_health(),
        "calibration": m.metrics or {"note": "cold-start prior (run `make eval` to fit)"},
        "thresholds": m.thresholds.to_dict(),
        "documents": [{"doc_id": d.doc_id, "publisher": d.publisher,
                       "title": d.title, "url": d.url} for d in corpus.MANIFEST],
    }


@app.get("/api/records")
def records():
    st = state()
    out = []
    for sku, r in st["records"].items():
        pub = sum(1 for a in r.attributes.values() if a.decision == DECISION_AUTO)
        rev = sum(1 for a in r.attributes.values() if a.decision == DECISION_REVIEW)
        out.append({"sku": sku, "mpn": r.mpn, "brand": r.brand,
                    "product_class": r.product_class,
                    "description": r.input_description,
                    "attributes": len(r.attributes), "published": pub,
                    "in_review": rev,
                    "conflicts": sum(1 for a in r.attributes.values() if a.conflicting),
                    "readiness": ai_readiness(r)["score"]})
    return out


@app.get("/api/records/{sku}")
def record(sku: str):
    r = state()["records"].get(sku)
    if not r:
        raise HTTPException(404, f"unknown sku {sku}")
    pclass = schema.get(r.product_class)
    d = r.to_dict()
    for name, a in d["attributes"].items():
        spec = pclass.by_name(name)
        a["label"] = spec.label if spec else name
        a["candidate_count"] = len(a.get("candidates", []))
        a["quarantined"] = [c["contaminated_by"] for c in a.get("candidates", [])
                            if c.get("contaminated_by")]
        a["rejected_provenance"] = [c["dropped_reason"] for c in a.get("candidates", [])
                                    if c.get("dropped_reason")][:3]
        a.pop("candidates", None)
    d["readiness"] = ai_readiness(r)
    d["audit"] = audit_trail(sku)[:20]
    return d


@app.get("/api/queue")
def queue():
    """Review queue ranked by risk, not by arrival.

    risk = uncertainty x safety weight x conflict weight. A reviewer with an hour
    should spend it where a wrong value does the most damage.
    """
    st = state()
    items = []
    for sku, r in st["records"].items():
        for name, a in r.attributes.items():
            if a.decision != DECISION_REVIEW:
                continue
            risk = (1.0 - a.confidence) * (3.0 if a.safety_critical else 1.0) \
                * (2.0 if a.conflicting else 1.0)
            items.append({
                "sku": sku, "mpn": r.mpn, "brand": r.brand, "attribute": name,
                "display": a.display, "value": a.value,
                "confidence": a.confidence, "safety_critical": a.safety_critical,
                "conflicting": a.conflicting, "conflict_values": a.conflict_values,
                "reasons": a.reasons, "rule_violations": a.rule_violations,
                "risk": round(risk, 4),
                "evidence": a.evidence.to_dict() if a.evidence else None,
            })
    items.sort(key=lambda x: -x["risk"])
    return items


@app.get("/api/evidence/{sku}/{attribute}.png")
def evidence_png(sku: str, attribute: str):
    """The source page, with the supporting sentence highlighted."""
    r = state()["records"].get(sku)
    if not r or attribute not in r.attributes:
        raise HTTPException(404, "no such attribute")
    ev = r.attributes[attribute].evidence
    if not ev:
        raise HTTPException(404, "attribute has no evidence")
    doc = ingest(ev.doc_id)
    focus = ev.focus_text if ev.focus_start >= 0 else ""
    png = doc.render_page_png(ev.page, highlight=(ev.quote or focus))
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=600"})


@app.get("/api/publish/{sku}")
def publish(sku: str):
    r = state()["records"].get(sku)
    if not r:
        raise HTTPException(404, f"unknown sku {sku}")
    return {"commerce": commerce_payload(r), "json_ld": json_ld(r),
            "readiness": ai_readiness(r)}


class ReviewIn(BaseModel):
    sku: str
    attribute: str
    action: str                 # ACCEPT | REJECT | CORRECT
    actor: str = "reviewer"
    corrected_value: str = ""
    note: str = ""


@app.post("/api/review")
def review(body: ReviewIn):
    if body.action not in {"ACCEPT", "REJECT", "CORRECT"}:
        raise HTTPException(400, "action must be ACCEPT, REJECT or CORRECT")
    r = state()["records"].get(body.sku)
    if not r or body.attribute not in r.attributes:
        raise HTTPException(404, "no such attribute")
    record_review(body.sku, body.attribute, body.action, body.actor,
                  body.corrected_value, body.note)
    a = r.attributes[body.attribute]
    if body.action == "ACCEPT":
        a.decision = DECISION_AUTO
        a.reasons.append(f"accepted by {body.actor}")
    elif body.action == "REJECT":
        a.decision = "REJECT"
        a.reasons.append(f"rejected by {body.actor}")
    else:
        a.decision = DECISION_AUTO
        a.display = body.corrected_value
        a.reasons.append(f"corrected to {body.corrected_value} by {body.actor}")
    return {"ok": True, "decision": a.decision}


@app.get("/api/metrics")
def metrics():
    p = config.EVAL_OUT / "metrics.json"
    if not p.exists():
        return JSONResponse({"error": "run `make eval` first"}, status_code=404)
    return json.loads(p.read_text())
