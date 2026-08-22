# SpecLedger

**Chain of custody for product data.** Every published attribute traces to a
highlighted sentence in a source datasheet, carries a calibrated confidence, and
auto-publishes only when it is provably safe to.

Team **Unisol** · UniHack 2026 — *AI-Powered Product Intelligence for Industrial Commerce*

---

## The problem, concretely

Vishay's rectifier datasheet covers seven part numbers in one table:

```
PARAMETER                              SYMBOL  1N4001 1N4002 1N4003 1N4004 1N4005 1N4006 1N4007  UNIT
Maximum repetitive peak reverse voltage VRRM    50     100    200    400    600    800    1000    V
```

Extracted to flat text, the part numbers and their values land in **separate
rows**. Getting 1N4001 right means knowing it is column 0 and reading `50` — not
the last number, not the largest. Publish `1000 V` for a 1N4001 and a customer
puts a 50 V part on a 600 V rail. It fails short, and something catches fire.

The LM317L datasheet contains a second trap, in prose:

> For higher output current requirements, see LM317M (500mA) and LM317 (1.5A)

Search that document for "output current" and you get two hits. One is the part's
real 100 mA rating. The other publishes **1.5 A for a 100 mA regulator** — a 15×
overstatement of a safety-critical value, sourced from a genuine sentence in the
manufacturer's own PDF.

These are not synthetic. They are in the files this repo downloads.

## Why this and not another generator

Unilog already ships generation — HyperScale's Product Description Agent is in
CX1 PIM today. Generation is solved. What is not solved is knowing **which
generated values are safe to publish without a human reading them**. At 11M+ SKUs
and ~20 attributes each, review is the cost centre, and it is why enrichment is
still manual.

So SpecLedger does not compete with generation. It makes generation shippable:

> **76.9% of attributes auto-publish at 98.6% measured precision, 100% on
> safety-critical values. Every published value cites a verified source span.**

## Results

Measured on 12 SKUs across 7 real vendor datasheets (Vishay, Diodes Inc, Texas
Instruments), graded against 98 labels transcribed from those PDFs, with the LLM
extractor (Amazon Nova Lite, via AWS Bedrock) live in the panel. Both arms run
the same inputs and the same documents. Reproduce with `make eval` after adding
AWS credentials to `.env` (see Quickstart).

| metric | naive extraction | SpecLedger |
|---|---:|---:|
| coverage | 92.9% | 92.9% |
| auto-publish rate | 100% | 80.0% |
| **precision on published** | **69.2%** | **98.7%** |
| **safety-critical precision** | **81.2%** | **100%** |
| **wrong values published** | **28** | **1** |
| queued for human review | 0 | 19 |
| calibration error (ECE) | 0.308 | 0.037 |

The naive arm publishes everything it finds, which is what "just call an LLM"
looks like in production: 28 wrong specs shipped, no signal about which.

**The LLM's real contribution isn't in this table.** Seven of the 98 gold labels
(`adjustable`, `polarity`) test attributes stated only in free narrative prose —
"The LM317 is an adjustable ... positive-voltage regulator" — with no table or
label/value line for a regex to anchor on. The deterministic extractors cannot
reach these at all; the naive arm's coverage on them is zero. Nova Lite reads
them correctly, verified against the same evidence gate as everything else, and
adds them to `graded attributes` (91 → 95) without moving `wrong values
published` (still 1, the same pre-existing error, untouched) or safety-critical
precision (still 100%). That is the shape of the win: strictly additive recall,
gated by the same verification everything else goes through.

**Risk–coverage frontier** — the knob a distributor actually turns:

| threshold | coverage | precision |
|---:|---:|---:|
| 0.00 | 100% | 94.7% |
| 0.46 | 95.8% | 98.9% |
| 0.90 | 94.7% | 98.9% |
| 0.95 | 83.2% | 98.7% |
| 0.98 | 13.7% | 92.3% |

## Quickstart

```bash
make setup && make fetch && make eval && make run
```

Then open <http://127.0.0.1:8077>. No API key, no database server, no container.
`make fetch` downloads 7 datasheets (~10 MB) from vendor sites.

The server opens the port immediately and warms the catalog in a background
thread; the cockpit shows a loading state until it's ready rather than hanging
with no explanation. With no LLM credentials this takes a few seconds. With
`ANTHROPIC_API_KEY` or AWS credentials configured (see `.env.example`), the LLM
extractor joins the panel and this first warm-up makes real API calls with
self-consistency sampling, which can take a few minutes on the full 12-SKU
catalog — a one-time cost per server start, not per request.

## How it works

```
sparse SKU (mpn + brand + one marketing line)
  │
  ├─ 1  INGEST         PDF → text with exact char offsets + page map (PyMuPDF)
  │
  ├─ 2  SEGMENT        datasheets have canonical sections, and each has authority
  │                    over different claims. Absolute Maximum Ratings states
  │                    ratings; Electrical Characteristics states TEST CONDITIONS
  │                    that look identical; Typical Characteristics is graphs and
  │                    speaks for nothing.
  │
  ├─ 3  EXTRACT        a panel of strategies, each emitting a verbatim quote:
  │                      table_column  resolves WHICH COLUMN of a series table
  │                                    belongs to this part  ← defeats trap #1
  │                      range         two-ended ranges, unit required from the
  │                                    document, never assumed from the schema
  │                      inline_spec   label/value lines, dimension-guarded
  │                      llm           Amazon Nova Lite via Bedrock, same
  │                                    contract (needs AWS credentials)
  │
  ├─ 4  GATE           every quote is re-checked against the document bytes.
  │                    Not found → the value is FABRICATED and is dropped.
  │                    Guards: sibling contamination  ← defeats trap #2
  │                            graph-axis rejection, section authority
  │
  ├─ 5  NORMALIZE      Pint. 100mA and 0.1A become one comparable number.
  │
  ├─ 6  ARBITRATE      cluster values across sources; weight by evidence quality,
  │                    source authority and brand match — never by raw count.
  │                    Cross-vendor disagreement is escalated, never resolved.
  │
  ├─ 7  RULES          physics as a free validator: surge > continuous,
  │                    Vin_max > Vout_max, Tmin < Tmax.
  │
  ├─ 8  CALIBRATE      logistic regression over 11 evidence features → P(correct),
  │                    threshold chosen to meet a precision floor on OUT-OF-FOLD
  │                    predictions → AUTO_PUBLISH / REVIEW / REJECT
  │
  └─ 9  PUBLISH        commerce payload + schema.org JSON-LD + audit trail
```

### Design decisions worth defending

**The differentiator does not depend on the LLM.** The gate, arbitration,
normalization and calibration are deterministic. The LLM is a pluggable candidate
*generator*; the confidence features describe the **evidence**, not the generator,
which is why the same calibration holds whether candidates came from regexes or
from the LLM, and why swapping the backend (Nova Lite today, via Bedrock) does
not invalidate anything measured here.

**Units must come from the document.** An early version defaulted a missing unit
to the schema's unit, and confidently published a thermal-resistance figure as a
temperature range. Two bare numbers near the word "temperature" are not a
temperature range.

**Ambiguity is surfaced, not resolved.** When one document yields two defensible
values for one attribute, both are emitted and the conflict routes to a human.
This *lowers* the auto-publish rate, correctly.

**A part number is not a product.** Vishay's 1N4001 and Diodes Inc's 1N4001 are
different parts sharing a JEDEC number, and they genuinely differ — VF of 1.1 V
vs 1.0 V, Tmin of −50 °C vs −65 °C. For a Vishay-branded SKU the Vishay datasheet
is authoritative and the other is a cross-reference.

**Safety-critical attributes can never be easier to publish.** The threshold
search can return 0.0 when a small gold set happens to contain no safety errors.
Absence of observed error is not evidence of safety, so a monotonicity constraint
forbids it.

## Honest limitations

- **The gold set is 91 labels over 12 SKUs.** You cannot empirically certify 99%
  precision at that size — one high-scoring error caps coverage at ~1%, which is
  why the default floor is 98% and the full frontier is published rather than a
  single flattering point.
- **One error survives**: LM1117 `package` reads `TO-252`, which appears in the
  document but is not among its offered packages. It is listed in `make eval`
  output rather than tuned away.
- **`is_conflicting` learned a positive weight** (+0.28) — wrong-signed, a
  small-sample artifact. It is harmless because `decide()` gates on conflict
  unconditionally, but it needs a monotonicity constraint at scale.
- **Two product classes, electronic components.** The schema registry is the
  extension point; PVF and electrical are the same shape of problem.
- **SQLite, not Postgres.** Deliberate for a POC a judge must be able to run.
- **The LLM extractor runs live against AWS Bedrock (Amazon Nova Lite)** and its
  measured contribution is in the table above. It ran second, behind three
  deterministic extractors that already resolve the great majority of attributes
  correctly on this corpus — so its measurable effect is concentrated on
  attributes those extractors structurally cannot reach (free-text facts with no
  table or label/value line), not on raising precision, which was already high.
  `make llm-check` verifies connectivity without making a full pipeline run.

## Layout

```
specledger/   corpus · ingest · sections · extract · verify · normalize
              rules · arbitrate · confidence · pipeline · store · publish · llm
api/          FastAPI: records, review queue, evidence PNGs, metrics
web/          Review Cockpit (single file, no build step)
eval/         run_eval.py — the numbers above
data/gold/    91 labels transcribed from the source PDFs
tests/        30 tests; test_evidence_gate.py is the important one
```

## Sources

Corpus URLs and SHA-256 are pinned in `specledger/corpus.py`. Datasheets are
copyrighted and are fetched at setup, not vendored.
