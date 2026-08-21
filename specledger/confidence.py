"""Calibrated confidence and selective abstention.

An LLM's self-reported confidence is not a probability -- it is badly calibrated
and cannot be budgeted against. What a distributor needs is: "publish everything
above threshold t, and I promise at least P precision on what I publish."

So confidence here is a LEARNED function of evidence-quality features, fitted on a
held-out gold set, and the publish threshold is chosen to satisfy a precision floor
rather than picked by eye. Safety-critical attributes get their own, stricter floor.

Features are deliberately model-independent -- they describe the EVIDENCE, not the
generator. That is why the same calibration holds whether candidates came from
regexes or from Claude.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from . import config
from .models import (ResolvedAttribute, DECISION_AUTO, DECISION_REVIEW, DECISION_REJECT,
                     MATCH_EXACT, MATCH_NORMALIZED, MATCH_RELOCATED)
from .arbitrate import authority_weight

FEATURES = [
    "extractor_precision",     # how trustworthy the winning strategy is
    "match_quality",           # how cleanly the quote matched the source bytes
    "agreeing_sources",        # independent documents supporting the value
    "is_conflicting",          # cross-document disagreement
    "n_competing",             # how many rival values existed
    "rule_violations",         # cross-attribute physics violations
    "authority",               # weight of the winning source tier
    "column_resolved",         # value came from a resolved series-table column
    "n_quarantined",           # candidates killed by the contamination guard
    "has_focus",               # evidence pins an exact cell, not just a row
    "self_consistency",        # agreement across independent LLM samples; always
                               # 1.0 for deterministic extractors, which are
                               # reproducible by construction
]

_MATCH_SCORE = {MATCH_EXACT: 1.0, MATCH_NORMALIZED: 0.9, MATCH_RELOCATED: 0.55}


def featurize(attr: ResolvedAttribute) -> np.ndarray:
    ev = attr.evidence
    win = None
    for c in attr.candidates:
        if c.evidence is ev:
            win = c
            break
    prec = float(win.precision) if win else 0.0
    return np.array([
        prec / 3.0,
        _MATCH_SCORE.get(ev.match_mode, 0.0) if ev else 0.0,
        min(attr.agreeing_sources, 4) / 4.0,
        1.0 if attr.conflicting else 0.0,
        min(len(attr.conflict_values), 4) / 4.0,
        min(len(attr.rule_violations), 3) / 3.0,
        authority_weight(win.tier) if win else 0.0,
        1.0 if (win and win.column_index >= 0) else 0.0,
        min(sum(1 for c in attr.candidates if c.contaminated_by), 4) / 4.0,
        1.0 if (ev and ev.focus_start >= 0) else 0.0,
        float(win.self_consistency) if win else 1.0,
    ], dtype=float)


# Transparent prior used before any gold set exists. Weights are hand-set and
# readable on purpose: this is the "no training data yet" cold-start path, and a
# reviewer should be able to audit it without loading a pickle.
PRIOR_W = np.array([1.6, 2.2, 1.5, -2.6, -0.9, -2.4, 1.0, 1.3, -0.5, 0.6, 1.1])
PRIOR_B = -1.9
assert len(PRIOR_W) == len(FEATURES), (
    f"cold-start prior has {len(PRIOR_W)} weights but there are {len(FEATURES)} "
    f"features; they must stay in lockstep")


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


@dataclass
class Thresholds:
    general: float = 0.90
    safety: float = 0.97
    reject_below: float = 0.25

    def to_dict(self):
        return {"general": self.general, "safety": self.safety,
                "reject_below": self.reject_below}


class ConfidenceModel:
    """Logistic regression over evidence features, with a readable cold-start prior."""

    def __init__(self):
        self.clf = None
        self.thresholds = Thresholds()
        self.trained = False
        self.metrics: dict = {}

    # ---------- scoring ----------
    def score(self, attr: ResolvedAttribute) -> float:
        x = featurize(attr).reshape(1, -1)
        if self.trained and self.clf is not None:
            return float(self.clf.predict_proba(x)[0, 1])
        return float(_sigmoid(x @ PRIOR_W + PRIOR_B)[0])

    def decide(self, attr: ResolvedAttribute) -> tuple[str, float]:
        p = self.score(attr)
        floor = self.thresholds.safety if attr.safety_critical else self.thresholds.general
        if any(v.startswith("[error]") for v in attr.rule_violations):
            return DECISION_REVIEW, p
        if p < self.thresholds.reject_below:
            return DECISION_REJECT, p
        if p >= floor and not attr.conflicting:
            return DECISION_AUTO, p
        return DECISION_REVIEW, p

    def apply(self, attr: ResolvedAttribute) -> ResolvedAttribute:
        decision, p = self.decide(attr)
        attr.confidence, attr.decision = round(p, 4), decision
        if decision == DECISION_AUTO:
            attr.reasons.append(
                f"auto-published: confidence {p:.3f} >= "
                f"{'safety ' if attr.safety_critical else ''}floor "
                f"{(self.thresholds.safety if attr.safety_critical else self.thresholds.general):.3f}")
        elif decision == DECISION_REVIEW:
            attr.reasons.append(f"routed to review: confidence {p:.3f}")
        else:
            attr.reasons.append(f"rejected: confidence {p:.3f} below floor")
        return attr

    # ---------- fitting ----------
    def fit(self, X: np.ndarray, y: np.ndarray, target: float = None,
            target_safety: float = None, safety_mask: np.ndarray = None):
        """Fit, then pick thresholds that meet a precision floor on out-of-fold
        predictions. Using OOF rather than in-sample predictions is what keeps the
        promised precision honest on a gold set this small."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold

        target = target or config.TARGET_PRECISION
        target_safety = target_safety or config.TARGET_PRECISION_SAFETY
        y = y.astype(int)

        if len(np.unique(y)) < 2:
            self.trained = False
            self.metrics = {"note": "gold set has a single class; keeping prior"}
            return self

        n_splits = int(min(5, max(2, np.bincount(y).min())))
        oof = np.zeros(len(y), dtype=float)
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=17)
        for tr, te in skf.split(X, y):
            m = LogisticRegression(C=1.0, max_iter=2000)
            m.fit(X[tr], y[tr])
            oof[te] = m.predict_proba(X[te])[:, 1]

        self.clf = LogisticRegression(C=1.0, max_iter=2000)
        self.clf.fit(X, y)
        self.trained = True

        gen_mask = ~safety_mask if safety_mask is not None else np.ones(len(y), bool)
        general = pick_threshold(oof[gen_mask], y[gen_mask], target)
        safety = (pick_threshold(oof[safety_mask], y[safety_mask], target_safety)
                  if safety_mask is not None and safety_mask.any() else 0.97)
        # A gold set this small can easily contain zero safety-critical errors,
        # and the search would then happily return 0.0 -- "publish everything".
        # Absence of observed error is not evidence of safety, so we constrain:
        # a safety-critical attribute is never easier to publish than a general
        # one, and never clears a bar below SAFETY_FLOOR.
        SAFETY_FLOOR = 0.60
        safety = max(safety, general, SAFETY_FLOOR)
        self.thresholds = Thresholds(general=general, safety=safety, reject_below=0.25)
        self.metrics = {
            "n": int(len(y)),
            "positives": int(y.sum()),
            "oof_auc": _auc(y, oof),
            "oof_ece": expected_calibration_error(y, oof),
            "thresholds": self.thresholds.to_dict(),
            "coverage_at_threshold": float((oof >= self.thresholds.general).mean()),
        }
        self._oof = oof
        return self

    # ---------- persistence ----------
    def save(self, path=None):
        path = path or (config.DATA / "confidence_model.json")
        blob = {
            "trained": self.trained,
            "thresholds": self.thresholds.to_dict(),
            "metrics": self.metrics,
            "coef": self.clf.coef_[0].tolist() if self.trained else None,
            "intercept": float(self.clf.intercept_[0]) if self.trained else None,
            "features": FEATURES,
        }
        path.write_text(json.dumps(blob, indent=2))
        return path

    @classmethod
    def load(cls, path=None):
        from sklearn.linear_model import LogisticRegression
        path = path or (config.DATA / "confidence_model.json")
        m = cls()
        if not path.exists():
            return m
        blob = json.loads(path.read_text())
        t = blob.get("thresholds", {})
        m.thresholds = Thresholds(t.get("general", 0.9), t.get("safety", 0.97),
                                  t.get("reject_below", 0.25))
        m.metrics = blob.get("metrics", {})
        # A persisted model and the feature builder can drift apart: add a feature
        # and a stale file still loads, then dies at matmul time. Validate the
        # schema and fall back to the readable prior rather than failing at
        # predict time, and make the staleness visible instead of silent.
        saved_features = blob.get("features") or []
        if saved_features and list(saved_features) != list(FEATURES):
            m.metrics = {
                "note": "saved model is stale (feature schema changed); using the "
                        "cold-start prior. Run `make eval` to refit.",
                "saved_features": len(saved_features),
                "current_features": len(FEATURES),
            }
            return m
        if blob.get("trained") and blob.get("coef"):
            if len(blob["coef"]) != len(FEATURES):
                m.metrics = {"note": "coefficient count does not match FEATURES; "
                                     "using the cold-start prior. Run `make eval`."}
                return m
            clf = LogisticRegression()
            clf.coef_ = np.array([blob["coef"]])
            clf.intercept_ = np.array([blob["intercept"]])
            clf.classes_ = np.array([0, 1])
            m.clf, m.trained = clf, True
        return m


def pick_threshold(p: np.ndarray, y: np.ndarray, target_precision: float) -> float:
    """Lowest threshold whose precision meets the floor -- i.e. maximise coverage
    subject to a precision constraint. Returns 1.01 if the floor is unreachable,
    which correctly means "auto-publish nothing"."""
    if len(p) == 0:
        return 0.9
    best = 1.01
    for t in np.unique(np.round(np.concatenate([p, [0.0, 1.0]]), 4))[::-1]:
        sel = p >= t
        if sel.sum() == 0:
            continue
        prec = y[sel].mean()
        if prec >= target_precision:
            best = float(t)
    return best


def risk_coverage_curve(p: np.ndarray, y: np.ndarray, points: int = 60):
    """(threshold, coverage, precision) as the abstention threshold sweeps."""
    out = []
    for t in np.linspace(0.0, 1.0, points):
        sel = p >= t
        cov = float(sel.mean())
        prec = float(y[sel].mean()) if sel.sum() else 1.0
        out.append((float(t), cov, prec))
    return out


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """ECE: mean gap between stated confidence and observed accuracy."""
    y = np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    ece, n = 0.0, len(y)
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= 1.0)
        if m.sum() == 0:
            continue
        ece += (m.sum() / n) * abs(y[m].mean() - p[m].mean())
    return float(ece)


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    try:
        from sklearn.metrics import roc_auc_score
        return float(roc_auc_score(y, p))
    except Exception:
        return float("nan")
