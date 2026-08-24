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

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e .[dev]
	maturin develop --release

test:
	$(PY) -m pytest -q

data:
	$(PY) scripts/notimpl.py 1 "make data"

eval:
	$(PY) scripts/notimpl.py 2 "make eval"

demo:
	$(PY) scripts/notimpl.py 2 "make demo"

bench:
	$(PY) scripts/notimpl.py 4 "make bench"

all:
	$(PY) scripts/notimpl.py 6 "make all"
