"""Measure the thing we claim.

Both arms run over the same catalog and the same source PDFs, and are graded
against labels transcribed from those PDFs. The confidence model is fitted here
with out-of-fold predictions, so the precision floor it promises is an honest
estimate rather than an in-sample one.

A note on what "error" means for this corpus. The deterministic extractors quote
real document text, so outright fabrication is rare; the dominant failure mode is
MISATTRIBUTION -- true text read for the wrong part, the wrong table row, or the
wrong operating condition. That is the harder problem and it is what the numbers
below measure. The evidence gate's ability to catch outright fabrication is
covered separately, in tests/test_evidence_gate.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from specledger import config, schema                      # noqa: E402
from specledger.catalog import CATALOG                     # noqa: E402
from specledger.confidence import (ConfidenceModel, featurize, FEATURES,  # noqa: E402
                                   expected_calibration_error, risk_coverage_curve,
                                   pick_threshold, _auc)
from specledger.models import DECISION_AUTO, DECISION_REVIEW  # noqa: E402
from specledger.pipeline import enrich_all                 # noqa: E402

GOLD = json.loads((config.GOLD / "gold.json").read_text())["labels"]
REL_TOL = 0.02


def matches(got, want) -> bool:
    if want is None or got is None:
        return False
    options = want if isinstance(want, list) else [want]
    for w in options:
        if isinstance(w, bool) or isinstance(got, bool):
            if bool(got) == bool(w):
                return True
        elif isinstance(w, (int, float)) and isinstance(got, (int, float)):
            if abs(float(got) - float(w)) <= REL_TOL * max(1.0, abs(float(w))):
                return True
        else:
            a, b = str(got).upper().replace(" ", ""), str(w).upper().replace(" ", "")
            if a == b or a.startswith(b) or b.startswith(a):
                return True
    return False


def grade(records) -> list[dict]:
    rows = []
    for r in records:
        gold = GOLD.get(r.sku, {})
        for name, attr in r.attributes.items():
            if name not in gold:
                continue                      # unlabelled -> excluded from metrics
            rows.append({
                "sku": r.sku, "mpn": r.mpn, "attribute": name,
                "value": attr.value, "display": attr.display,
                "gold": gold[name],
                "correct": bool(matches(attr.value, gold[name])),
                "decision": attr.decision, "confidence": attr.confidence,
                "safety": attr.safety_critical, "attr": attr,
            })
    return rows


def coverage_of(records) -> tuple[int, int]:
    got = sum(1 for r in records for n in r.attributes
              if n in GOLD.get(r.sku, {}) and r.attributes[n].value is not None)
    total = sum(len(GOLD.get(r.sku, {})) for r in records)
    return got, total


def arm_metrics(rows, records, label) -> dict:
    got, total = coverage_of(records)
    pub = [r for r in rows if r["decision"] == DECISION_AUTO]
    rev = [r for r in rows if r["decision"] == DECISION_REVIEW]
    allv = rows
    p = np.array([r["confidence"] for r in rows]) if rows else np.zeros(0)
    y = np.array([r["correct"] for r in rows], float) if rows else np.zeros(0)
    safety_pub = [r for r in pub if r["safety"]]
    return {
        "arm": label,
        "attributes_labelled": total,
        "attributes_valued": got,
        "coverage": round(got / total, 4) if total else 0.0,
        "accuracy_all_emitted": round(float(np.mean([r["correct"] for r in allv])), 4) if allv else 0.0,
        "auto_published": len(pub),
        "auto_publish_rate": round(len(pub) / len(rows), 4) if rows else 0.0,
        "precision_on_published": round(float(np.mean([r["correct"] for r in pub])), 4) if pub else None,
        "safety_precision_on_published": round(float(np.mean([r["correct"] for r in safety_pub])), 4) if safety_pub else None,
        "wrong_values_published": int(sum(1 for r in pub if not r["correct"])),
        "queued_for_review": len(rev),
        "review_burden": round(len(rev) / len(rows), 4) if rows else 0.0,
        "ece": round(expected_calibration_error(y, p), 4) if len(y) else None,
    }


def main():
    print("Running naive baseline ...")
    naive_recs = enrich_all(mode="naive")
    naive_rows = grade(naive_recs)

    print("Running SpecLedger (cold-start prior) ...")
    cold = ConfidenceModel()
    sl_recs = enrich_all(model=cold)
    sl_rows = grade(sl_recs)

    X = np.array([featurize(r["attr"]) for r in sl_rows])
    y = np.array([r["correct"] for r in sl_rows], int)
    safety_mask = np.array([r["safety"] for r in sl_rows], bool)

    print(f"Fitting calibrator on {len(y)} graded attributes "
          f"({y.sum()} correct / {len(y)-y.sum()} wrong) ...")
    model = ConfidenceModel().fit(X, y, safety_mask=safety_mask)
    model.save()

    if model.trained:
        oof = model._oof
        for row, pr in zip(sl_rows, oof):
            row["confidence"] = float(pr)
            floor = model.thresholds.safety if row["safety"] else model.thresholds.general
            a = row["attr"]
            bad_rule = any(v.startswith("[error]") for v in a.rule_violations)
            row["decision"] = (DECISION_AUTO
                               if (pr >= floor and not a.conflicting and not bad_rule)
                               else DECISION_REVIEW)
    else:
        oof = np.array([r["confidence"] for r in sl_rows])

    m_naive = arm_metrics(naive_rows, naive_recs, "naive baseline")
    m_sl = arm_metrics(sl_rows, sl_recs, "SpecLedger (out-of-fold)")

    curve = risk_coverage_curve(oof, y.astype(float))
    out = {
        "corpus": {"skus": len(CATALOG), "documents": 7,
                   "product_classes": sorted(schema.REGISTRY)},
        "target_precision": config.TARGET_PRECISION,
        "target_precision_safety": config.TARGET_PRECISION_SAFETY,
        "arms": [m_naive, m_sl],
        "calibration": model.metrics,
        "feature_weights": (dict(zip(FEATURES, np.round(model.clf.coef_[0], 3).tolist()))
                            if model.trained else None),
        "risk_coverage_curve": [{"threshold": round(t, 3), "coverage": round(c, 4),
                                 "precision": round(p, 4)} for t, c, p in curve],
        "errors_published_by_specledger": [
            {"sku": r["sku"], "attribute": r["attribute"], "got": r["display"],
             "gold": r["gold"], "confidence": round(r["confidence"], 3)}
            for r in sl_rows if r["decision"] == DECISION_AUTO and not r["correct"]],
        "errors_published_by_naive": [
            {"sku": r["sku"], "attribute": r["attribute"], "got": r["display"],
             "gold": r["gold"]}
            for r in naive_rows if r["decision"] == DECISION_AUTO and not r["correct"]][:40],
    }
    config.EVAL_OUT.mkdir(parents=True, exist_ok=True)
    (config.EVAL_OUT / "metrics.json").write_text(json.dumps(out, indent=2, default=str))

    # ---- console report ----
    def row(k, a, b):
        print(f"  {k:<34} {str(a):>14} {str(b):>18}")
    print("\n" + "=" * 70)
    print("  SpecLedger evaluation -- real vendor datasheets, gold from source")
    print("=" * 70)
    print(f"  {'metric':<34} {'naive':>14} {'SpecLedger':>18}")
    print("  " + "-" * 66)
    row("attributes graded", len(naive_rows), len(sl_rows))
    row("coverage", f"{m_naive['coverage']:.1%}", f"{m_sl['coverage']:.1%}")
    row("auto-publish rate", f"{m_naive['auto_publish_rate']:.1%}", f"{m_sl['auto_publish_rate']:.1%}")
    row("precision on published",
        f"{(m_naive['precision_on_published'] or 0):.1%}",
        f"{(m_sl['precision_on_published'] or 0):.1%}")
    row("safety-critical precision",
        f"{(m_naive['safety_precision_on_published'] or 0):.1%}",
        f"{(m_sl['safety_precision_on_published'] or 0):.1%}")
    row("WRONG values published", m_naive["wrong_values_published"], m_sl["wrong_values_published"])
    row("queued for review", m_naive["queued_for_review"], m_sl["queued_for_review"])
    row("calibration error (ECE)", f"{m_naive['ece']:.3f}", f"{m_sl['ece']:.3f}")
    print("  " + "-" * 66)
    if model.trained:
        print(f"  OOF AUC {model.metrics['oof_auc']:.3f} | thresholds: "
              f"general {model.thresholds.general:.3f}, safety {model.thresholds.safety:.3f}")
    print("\n  risk-coverage frontier (sweep the abstention threshold):")
    print(f"    {'threshold':>10} {'coverage':>10} {'precision':>10}")
    seen = set()
    for pt in out["risk_coverage_curve"]:
        k = (round(pt["coverage"], 2), round(pt["precision"], 3))
        if k in seen or pt["coverage"] == 0:
            continue
        seen.add(k)
        print(f"    {pt['threshold']:>10.2f} {pt['coverage']:>9.1%} {pt['precision']:>10.1%}")
    print(f"\n  wrote {config.EVAL_OUT / 'metrics.json'}")
    if out["errors_published_by_specledger"]:
        print("\n  SpecLedger still published these incorrectly:")
        for e in out["errors_published_by_specledger"]:
            print(f"    {e['sku']:16s} {e['attribute']:20s} got={e['got']} gold={e['gold']}")
    return out


if __name__ == "__main__":
    main()
