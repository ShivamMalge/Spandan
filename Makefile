# Spandan task interface.
#
# Every target is declared here so the interface is reviewable up front.
#
# Recipes are deliberately one command per line with no shell operators, so they
# behave identically under cmd.exe, PowerShell and bash.

.PHONY: setup test data eval bench demo check all

PY := python
SEEDS ?= 3
ENGINE ?= python

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
	$(PY) -m spandan.eval.harness --data data --seeds $(SEEDS) --engine $(ENGINE) --json-out data/metrics.json

demo:
	$(PY) -m spandan.cli replay --data data --limit 20000

bench:
	$(PY) -m spandan.eval.bench --data data

# Every figure the documents assert, checked against data/metrics.json and
# data/manifest.json. Fails on the first figure that no longer reproduces.
check:
	$(PY) scripts/check_figures.py

# Everything deterministic, in dependency order. bench is separate on purpose:
# its numbers are machine-dependent by nature, while everything `all` produces
# must match the README exactly on any machine.
all: data test eval demo check
