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


def test_smoke_add_crosses_the_pyo3_boundary():
    # Deleted in Phase 3 along with the function it exercises.
    assert spandan_core._smoke_add(2, 3) == 5
