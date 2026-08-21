"""Paths and runtime settings. Everything is relative to the repo root so the
project is clonable and runnable with no absolute-path surprises."""
from __future__ import annotations
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIXTURES = DATA / "fixtures"
GOLD = DATA / "gold"
CACHE = DATA / "cache"
DB_PATH = DATA / "specledger.db"
EVAL_OUT = ROOT / "eval" / "out"

for _p in (DATA, FIXTURES, GOLD, CACHE, EVAL_OUT):
    _p.mkdir(parents=True, exist_ok=True)

PIPELINE_VERSION = "0.3.0"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.environ.get("SPECLEDGER_MODEL", "claude-sonnet-4-5")
LLM_AVAILABLE = bool(ANTHROPIC_API_KEY)

# Target precision floor for auto-publish. The business sets this; the calibrator
# then finds the confidence threshold that achieves it. This is the knob a
# distributor actually turns.
# 0.98, not 0.99, and deliberately so: with a 91-attribute gold set you cannot
# empirically certify 99% precision -- a single high-scoring error caps coverage
# at ~1%. The floor is an input the business sets; the eval publishes the whole
# risk-coverage frontier so the trade is visible rather than asserted.
TARGET_PRECISION = float(os.environ.get("SPECLEDGER_TARGET_PRECISION", "0.98"))
# Safety-critical attributes get a stricter floor -- a wrong interrupting rating
# starts fires, a wrong package type just annoys someone.
TARGET_PRECISION_SAFETY = float(os.environ.get("SPECLEDGER_TARGET_PRECISION_SAFETY", "0.99"))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
