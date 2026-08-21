VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.PHONY: help setup fetch run eval test demo clean reset llm-check

help:
	@echo "SpecLedger — verifiable product intelligence"
	@echo ""
	@echo "  make setup   create venv and install dependencies"
	@echo "  make fetch   download the source datasheet corpus (7 real vendor PDFs)"
	@echo "  make eval    run the evaluation: naive baseline vs SpecLedger, fit calibrator"
	@echo "  make run     start the API + Review Cockpit on http://127.0.0.1:8077"
	@echo "  make test    run the test suite"
	@echo "  make llm-check  verify the Bedrock/Anthropic backend is reachable"
	@echo "  make demo    setup + fetch + eval + test, then start the cockpit"

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) -q install --upgrade pip

setup: $(VENV)
	$(PIP) -q install -r requirements.txt
	@echo "ready. next: make fetch"

fetch:
	$(PY) -m specledger.corpus

llm-check:
	$(PY) -m specledger.llm

eval:
	$(PY) eval/run_eval.py

run:
	$(VENV)/bin/uvicorn api.main:app --port 8077 --reload

test:
	$(PY) -m pytest tests/ -q -p no:warnings

demo: setup fetch eval test
	@echo ""
	@echo "  cockpit starting on http://127.0.0.1:8077"
	@$(MAKE) run

clean:
	rm -rf data/cache data/specledger.db eval/out
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

reset: clean
	rm -rf data/fixtures data/corpus.lock.json data/confidence_model.json
