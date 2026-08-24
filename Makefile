# Spandan task interface.
#
# Every target is declared here in Phase 0 so the interface is reviewable up
# front. Targets whose phase has not been handed over exit non-zero via
# scripts/notimpl.py rather than silently succeeding.
#
# Recipes are deliberately one command per line with no shell operators, so they
# behave identically under cmd.exe, PowerShell and bash.

.PHONY: setup test data eval bench demo all

PY := python
SEEDS ?= 3

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e .[dev]
	maturin develop --release

test:
	$(PY) -m pytest -q

data:
	$(PY) -m spandan.gen.build --out data
	$(PY) -m spandan.gen.summary --data data

eval:
	$(PY) -m spandan.eval.harness --data data --seeds $(SEEDS) --json-out data/metrics.json

demo:
	$(PY) -m spandan.cli replay --data data --limit 20000

bench:
	$(PY) scripts/notimpl.py 4 "make bench"

all:
	$(PY) scripts/notimpl.py 6 "make all"
