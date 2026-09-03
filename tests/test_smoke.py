"""Phase 0 smoke tests: the toolchain builds and both packages import.

These prove the harness works, nothing about the detector. The real test suites
arrive with their phases (tests/test_gen.py in Phase 1, and so on).
"""

import spandan
import spandan_core


def test_python_package_imports():
    assert spandan.__version__ == "0.1.0"


def test_rust_extension_imports_and_reports_version():
    assert spandan_core.__version__ == "0.1.0"


def test_bench_imports_and_degrades_off_windows(monkeypatch):
    """`make bench` measures RSS through Win32 counters. Off Windows the module
    must still import and the throughput benches must still exist; only the
    memory accessors are unavailable, and they say so instead of crashing.

    numpy is imported first on the real platform: it reads `sys.platform` at
    its own import time and would call `os.uname()` on a faked Linux.
    """
    import importlib
    import sys

    import numpy  # noqa: F401  (loaded before the platform is faked)
    import spandan.eval  # noqa: F401

    monkeypatch.setattr(sys, "platform", "linux")
    sys.modules.pop("spandan.eval.bench", None)
    bench = importlib.import_module("spandan.eval.bench")

    assert bench.MEMORY_SUPPORTED is False
    assert callable(bench.bench_batches) and callable(bench.bench_streaming)
    import pytest

    with pytest.raises(NotImplementedError, match="Win32"):
        bench._current_rss_bytes()

    # And back on the real platform the counters read a real number.
    monkeypatch.setattr(sys, "platform", "win32")
    sys.modules.pop("spandan.eval.bench", None)
    bench = importlib.import_module("spandan.eval.bench")
    assert bench.MEMORY_SUPPORTED is True
    assert bench._current_rss_bytes() > 0


def test_the_phase_0_toolchain_probe_is_gone():
    # _smoke_add existed to prove the PyO3/maturin toolchain and PHASES.md
    # Phase 3 deletes it. Its absence is part of the phase's acceptance.
    assert not hasattr(spandan_core, "_smoke_add")
