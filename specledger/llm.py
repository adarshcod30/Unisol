"""LLM-backed extraction over AWS Bedrock, talking to Amazon Nova Lite by
default -- the same model AGENTIQ uses. A direct-to-provider API is kept as a
fallback path for local development, selected via SPECLEDGER_LLM_BACKEND.

The LLM is a candidate GENERATOR, not an oracle. It proposes values; verify.py
decides whether they survive. That separation is the whole architecture, and it
is what makes the backend swappable: nothing downstream of extract() cares
whether a candidate came from Nova, Claude, or a regex.

  * The model must return a VERBATIM quote, which we then locate ourselves.
  * "NOT_FOUND" is a first-class answer and is never penalised.
  * Every proposal is re-checked against the document bytes, so a fabricated
    citation is discarded deterministically however confident the model sounded.

Self-consistency: each extraction is sampled k times at non-zero temperature and
the agreement rate is recorded on the candidate. Sample disagreement is a real
uncertainty signal, unlike a model's self-reported confidence, and it feeds the
calibrator as a feature.

Credentials are read from the environment at call time. They are never logged,
never written into a record, and never enter the audit trail.
"""
from __future__ import annotations

import re
from collections import Counter

from . import config
from .ingest import IngestedDoc
from .models import Candidate, EvidenceSpan
from .normalize import normalize_candidate
from .schema import AttributeSpec

TOOL_NAME = "report_attribute"
TOOL_DESC = "Report one product attribute value with the exact evidence it came from."
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean",
                  "description": "false if the document does not state this attribute "
                                 "for THIS exact part number"},
        "value": {"type": "string",
                  "description": "the value exactly as written, including its unit"},
        "quote": {"type": "string",
                  "description": "VERBATIM text copied character-for-character from the "
                                 "excerpt that states this value. Never paraphrase and "
                                 "never reconstruct from memory."},
        "reasoning": {"type": "string",
                      "description": "one sentence on why this quote is about this part "
                                     "number specifically"},
    },
    "required": ["found"],
}

SYSTEM = """You extract structured product attributes from industrial datasheets.

Rules you must never break:
1. Copy `quote` VERBATIM from the supplied excerpt. It is checked against the source
   document character by character. A quote that does not appear there is discarded
   and counts as an error against you.
2. Series datasheets tabulate many part numbers at once. A row reading
   "VRRM 50 100 200 400 600 800 1000 V" under a header "1N4001 ... 1N4007" means
   1N4001 is 50 V and 1N4007 is 1000 V. Read the column for the requested part only.
   Never return the largest value because it looks like a maximum.
3. A datasheet often mentions SIBLING parts ("For higher current see LM317 (1.5A)").
   Those sentences are not evidence about the requested part.
4. Numbers beside axis labels in Typical Characteristics plots are tick marks, not
   ratings.
5. Test conditions ("TJ = 100°C") look like ratings but are the conditions a
   measurement was taken under. They are not the part's rating.
6. If the excerpt does not state this attribute for this exact part, return
   found=false. Abstaining is always acceptable and is never penalised. Guessing is.
"""


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------
class BedrockBackend:
    """AWS Bedrock Converse API. Credentials come from the standard boto3 chain."""
    name = "bedrock"

    def __init__(self, model: str | None = None, region: str | None = None):
        self.model = model or config.BEDROCK_MODEL
        self.region = region or config.AWS_REGION
        self._client = None

    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config as BotoConfig
            self._client = boto3.client(
                "bedrock-runtime", region_name=self.region,
                config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"},
                                  read_timeout=60, connect_timeout=10))
        return self._client

    def call(self, prompt: str, temperature: float) -> dict | None:
        resp = self.client().converse(
            modelId=self.model,
            system=[{"text": SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig={
                "tools": [{"toolSpec": {"name": TOOL_NAME, "description": TOOL_DESC,
                                        "inputSchema": {"json": TOOL_SCHEMA}}}],
                "toolChoice": {"tool": {"name": TOOL_NAME}},
            },
            inferenceConfig={"maxTokens": 700, "temperature": temperature},
        )
        return parse_bedrock(resp)


class AnthropicBackend:
    """Direct-to-provider fallback, used only when SPECLEDGER_LLM_BACKEND is set
    to something other than "bedrock". Bedrock/Nova is the default path."""
    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.model = model or config.LLM_MODEL or "claude-sonnet-4-5"
        self._client = None

    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        return self._client

    def call(self, prompt: str, temperature: float) -> dict | None:
        resp = self.client().messages.create(
            model=self.model, max_tokens=700, system=SYSTEM, temperature=temperature,
            tools=[{"name": TOOL_NAME, "description": TOOL_DESC,
                    "input_schema": TOOL_SCHEMA}],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": prompt}])
        return parse_anthropic(resp)


def parse_bedrock(resp: dict) -> dict | None:
    """Pull the tool payload out of a Converse response. Pure, so it is testable
    against a recorded response without any network or credentials."""
    try:
        blocks = resp["output"]["message"]["content"]
    except (KeyError, TypeError):
        return None
    for b in blocks:
        if isinstance(b, dict) and "toolUse" in b:
            return b["toolUse"].get("input")
    return None


def parse_anthropic(resp) -> dict | None:
    for b in getattr(resp, "content", []) or []:
        if getattr(b, "type", "") == "tool_use":
            return b.input
    return None


def make_backend(kind: str | None = None):
    kind = (kind or config.LLM_BACKEND).lower()
    return BedrockBackend() if kind == "bedrock" else AnthropicBackend()


def available() -> bool:
    return config.llm_available()


# --------------------------------------------------------------------------
class LLMExtractor:
    name = "llm"

    def __init__(self, backend=None, max_windows: int = 3, samples: int | None = None):
        self.backend = backend or make_backend()
        self.max_windows = max_windows
        self.samples = samples if samples is not None else config.LLM_SAMPLES
        self.calls = 0
        self.failures = 0

    def _windows(self, doc: IngestedDoc, spec: AttributeSpec) -> list[tuple[int, str]]:
        """Retrieve focused regions by alias so the model sees relevant context
        rather than a 60k-character datasheet."""
        out, seen = [], set()
        for alias in sorted(spec.aliases, key=len, reverse=True):
            for m in re.finditer(re.escape(alias), doc.text, re.IGNORECASE):
                start = max(0, m.start() - 400)
                end = min(len(doc.text), m.end() + 700)
                key = start // 500
                if key in seen:
                    continue
                seen.add(key)
                out.append((start, doc.text[start:end]))
                if len(out) >= self.max_windows:
                    return out
        return out

    def _prompt(self, mpn: str, spec: AttributeSpec, siblings: tuple, window: str) -> str:
        sib = ", ".join(s for s in siblings if s.upper() != mpn.upper()) or "none"
        return (f"Part number: {mpn}\n"
                f"Other part numbers covered by this document: {sib}\n"
                f"Attribute wanted: {spec.name} ({spec.label})\n"
                f"Expected unit: {spec.unit or 'n/a'}\n"
                f"{('Note: ' + spec.hint) if spec.hint else ''}\n\n"
                f"--- document excerpt ---\n{window}\n--- end excerpt ---")

    def extract(self, doc: IngestedDoc, mpn: str, spec: AttributeSpec,
                siblings: tuple, known_parts: set[str]) -> list[Candidate]:
        if not available():
            return []
        out: list[Candidate] = []
        for offset, window in self._windows(doc, spec):
            prompt = self._prompt(mpn, spec, siblings, window)
            payloads = []
            for i in range(max(1, self.samples)):
                temp = 0.0 if self.samples == 1 else (0.0 if i == 0 else 0.6)
                try:
                    self.calls += 1
                    p = self.backend.call(prompt, temp)
                except Exception:
                    self.failures += 1
                    continue
                if p and p.get("found") and (p.get("quote") or "").strip() \
                        and (p.get("value") or "").strip():
                    payloads.append(p)
            if not payloads:
                continue

            # self-consistency: how often did independent samples agree?
            counts = Counter(str(p.get("value", "")).strip().lower() for p in payloads)
            top_value, top_n = counts.most_common(1)[0]
            winner = next(p for p in payloads
                          if str(p.get("value", "")).strip().lower() == top_value)
            consistency = top_n / max(1, self.samples)

            quote = winner["quote"].strip()
            # Trust nothing: locate the quote ourselves. Invented text yields -1
            # and verify.py will mark the span NOT_FOUND.
            found_at = doc.find_quote(quote)
            start = found_at if found_at >= 0 else offset
            ev = EvidenceSpan(doc.doc.doc_id, doc.page_for_offset(start),
                              start, start + len(quote), quote)
            c = Candidate(spec.name, winner["value"].strip(), ev, self.name,
                          doc.doc.doc_id, doc.doc.authority)
            # Agreement across samples raises evidence strength, but an LLM
            # proposal never outranks a resolved series-table column.
            c.precision = 1.4 + 0.8 * consistency
            c.self_consistency = round(consistency, 3)
            c.samples = len(payloads)
            from .extract import contamination
            c.contaminated_by = contamination(window, mpn, known_parts)
            out.append(normalize_candidate(c, spec))
        return out


def panel(include_llm: bool | None = None):
    """Strategies to run. The LLM joins the deterministic panel when credentials
    are configured: it adds recall, and panel agreement adds confidence."""
    from .extract import DETERMINISTIC
    use = available() if include_llm is None else include_llm
    return list(DETERMINISTIC) + ([LLMExtractor()] if use else [])


def selfcheck() -> int:
    """Verify the LLM backend end to end. Never prints credential values."""
    import os
    print(f"backend        : {config.LLM_BACKEND}")
    if config.LLM_BACKEND == "bedrock":
        kid = os.environ.get("AWS_ACCESS_KEY_ID", "")
        print(f"region         : {config.AWS_REGION}")
        print(f"model          : {config.BEDROCK_MODEL}")
        print(f"access key id  : {(kid[:4] + '...' + kid[-4:]) if len(kid) > 8 else '(not set)'}")
        print(f"secret key     : {'set' if os.environ.get('AWS_SECRET_ACCESS_KEY') else '(not set)'}")
        print(f"session token  : {'set' if os.environ.get('AWS_SESSION_TOKEN') else 'not set'}")
    else:
        print(f"model          : {config.LLM_MODEL}")
        print(f"api key        : {'set' if config.ANTHROPIC_API_KEY else '(not set)'}")
    print(f"samples/extract: {config.LLM_SAMPLES}")

    if not available():
        print("\nstatus         : NOT CONFIGURED")
        print("  add credentials to .env, then re-run `make llm-check`.")
        return 1

    be = make_backend()
    probe = ("Part number: 1N4001\nAttribute wanted: reverse_voltage_max (Peak Reverse Voltage)\n"
             "Expected unit: V\n\n--- document excerpt ---\n"
             "MAXIMUM RATINGS\nPARAMETER SYMBOL 1N4001 1N4002 1N4007 UNIT\n"
             "Maximum repetitive peak reverse voltage VRRM 50 100 1000 V\n"
             "--- end excerpt ---")
    try:
        out = be.call(probe, 0.0)
    except Exception as e:
        msg = str(e)
        print(f"\nstatus         : FAILED\n  {type(e).__name__}: {msg[:300]}")
        low = msg.lower()
        if "could not connect" in low or "endpoint" in low:
            print("  -> check AWS_REGION is a region where you have Bedrock access.")
        if "accessdenied" in low.replace(" ", "") or "not authorized" in low:
            print("  -> the IAM user needs bedrock:InvokeModel on this model.")
        if "validationexception" in low.replace(" ", "") or "model identifier" in low:
            print("  -> that model id is not enabled in this region. Try an inference")
            print("     profile id prefixed with your region group, e.g. us./eu./apac.")
        if "expired" in low or "invalid" in low and "token" in low:
            print("  -> credentials look expired; refresh them.")
        return 2

    print(f"\nstatus         : OK")
    print(f"  probe returned : {out}")
    ok = out and str(out.get("value", "")).strip().startswith("50")
    print(f"  column trap    : {'PASSED (read 50 V, not 1000 V)' if ok else 'check the value above'}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selfcheck())
