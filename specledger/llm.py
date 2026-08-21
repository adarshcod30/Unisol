"""Claude-backed extraction, held to exactly the same evidence contract.

The LLM is a candidate GENERATOR, not an oracle. It proposes values; verify.py
decides whether they survive. That separation is the point of the architecture:

  * The model is asked for a verbatim quote and the offsets it came from.
  * "NOT_FOUND" is a first-class answer and is never penalised.
  * Anything it returns is re-checked against the document bytes, so a fabricated
    quote is caught deterministically no matter how confident the model sounded.

This is why the calibration in confidence.py transfers: its features describe the
EVIDENCE, not the generator, so swapping regexes for Claude does not invalidate
the thresholds. Add ANTHROPIC_API_KEY to .env and this extractor joins the panel;
without a key the deterministic strategies run alone and everything still works.
"""
from __future__ import annotations

import json
import re

from . import config
from .ingest import IngestedDoc
from .models import Candidate, EvidenceSpan
from .normalize import normalize_candidate
from .schema import AttributeSpec

EXTRACT_TOOL = {
    "name": "report_attribute",
    "description": "Report one product attribute value with the exact evidence it came from.",
    "input_schema": {
        "type": "object",
        "properties": {
            "found": {"type": "boolean",
                      "description": "false if the document does not state this "
                                     "attribute for THIS part number"},
            "value": {"type": "string",
                      "description": "the value exactly as written, with its unit"},
            "quote": {"type": "string",
                      "description": "VERBATIM text copied character-for-character from "
                                     "the document that states this value. Never "
                                     "paraphrase, never reconstruct from memory."},
            "reasoning": {"type": "string",
                          "description": "one sentence on why this quote is about this "
                                         "part number specifically"},
        },
        "required": ["found"],
    },
}

SYSTEM = """You extract structured product attributes from industrial datasheets.

Rules you must never break:
1. Copy the `quote` VERBATIM from the supplied text. It will be checked against the
   source document character by character. A quote that does not appear in the
   document is discarded and counts as an error.
2. Series datasheets tabulate many part numbers at once. A row like
   "VRRM 50 100 200 400 600 800 1000 V" with header "1N4001 ... 1N4007" means
   1N4001 is 50 V, NOT 1000 V. Read the column for the requested part only.
3. A datasheet may mention SIBLING parts ("see LM317M (500mA)"). Those sentences
   are not evidence about the requested part.
4. Values in Typical Characteristics plots are axis ticks, not ratings.
5. If the document does not state this attribute for this exact part, return
   found=false. Abstaining is always acceptable and is never penalised. Guessing is.
"""


def available() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


class LLMExtractor:
    name = "llm"

    def __init__(self, model: str | None = None, max_windows: int = 3):
        self.model = model or config.LLM_MODEL
        self.max_windows = max_windows
        self._client = None

    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        return self._client

    def _windows(self, doc: IngestedDoc, spec: AttributeSpec) -> list[tuple[int, str]]:
        """Retrieve candidate regions by alias, so the model sees focused context
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

    def extract(self, doc: IngestedDoc, mpn: str, spec: AttributeSpec,
                siblings: tuple, known_parts: set[str]) -> list[Candidate]:
        if not available():
            return []
        windows = self._windows(doc, spec)
        if not windows:
            return []
        out: list[Candidate] = []
        for offset, window in windows:
            sib = ", ".join(s for s in siblings if s.upper() != mpn.upper()) or "none"
            prompt = (
                f"Part number: {mpn}\n"
                f"Other part numbers covered by this document: {sib}\n"
                f"Attribute wanted: {spec.name} ({spec.label})\n"
                f"Expected unit: {spec.unit or 'n/a'}\n"
                f"{('Note: ' + spec.hint) if spec.hint else ''}\n\n"
                f"--- document excerpt ---\n{window}\n--- end excerpt ---"
            )
            try:
                resp = self.client().messages.create(
                    model=self.model, max_tokens=700, system=SYSTEM,
                    tools=[EXTRACT_TOOL],
                    tool_choice={"type": "tool", "name": "report_attribute"},
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as e:                       # network/quota/etc
                return [] if not out else out
            payload = next((b.input for b in resp.content
                            if getattr(b, "type", "") == "tool_use"), None)
            if not payload or not payload.get("found"):
                continue
            quote = (payload.get("quote") or "").strip()
            value = (payload.get("value") or "").strip()
            if not quote or not value:
                continue
            # Trust nothing: locate the quote ourselves. If the model invented it,
            # find_quote returns -1 and verify.py will mark it NOT_FOUND.
            found_at = doc.find_quote(quote)
            start = found_at if found_at >= 0 else offset
            ev = EvidenceSpan(doc.doc.doc_id, doc.page_for_offset(start),
                              start, start + len(quote), quote)
            c = Candidate(spec.name, value, ev, self.name, doc.doc.doc_id,
                          doc.doc.authority)
            c.precision = 2.0
            from .extract import contamination
            c.contaminated_by = contamination(window, mpn, known_parts)
            out.append(normalize_candidate(c, spec))
        return out


def panel(include_llm: bool | None = None):
    """Extraction strategies to run. The LLM joins the deterministic panel when a
    key is configured -- it adds recall; the panel's agreement adds confidence."""
    from .extract import DETERMINISTIC
    use = available() if include_llm is None else include_llm
    return list(DETERMINISTIC) + ([LLMExtractor()] if use else [])
