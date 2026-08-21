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

# ---- LLM backend -----------------------------------------------------------
# Two ways to reach Claude. Bedrock is the default because it is what the AWS
# credentials in .env are for; the direct Anthropic API is kept as an alternative.
# Credentials are ALWAYS read from the environment at call time and are never
# logged, serialised into a record, or written to the audit trail.
LLM_BACKEND = os.environ.get("SPECLEDGER_LLM_BACKEND", "bedrock").strip().lower()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.environ.get("SPECLEDGER_MODEL", "claude-sonnet-4-5")

AWS_REGION = (os.environ.get("AWS_REGION")
              or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
BEDROCK_MODEL = os.environ.get(
    "SPECLEDGER_BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
_AWS_KEYS_PRESENT = bool(os.environ.get("AWS_ACCESS_KEY_ID")
                         and os.environ.get("AWS_SECRET_ACCESS_KEY"))

# How many independent samples to draw per extraction. Agreement across samples
# is a genuine uncertainty signal and feeds the confidence model; 1 disables it.
LLM_SAMPLES = int(os.environ.get("SPECLEDGER_LLM_SAMPLES", "3"))


def llm_available() -> bool:
    if LLM_BACKEND == "bedrock":
        return _AWS_KEYS_PRESENT
    return bool(ANTHROPIC_API_KEY)


LLM_AVAILABLE = llm_available()

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
