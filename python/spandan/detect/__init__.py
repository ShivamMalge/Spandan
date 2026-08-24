"""Detection. Deterministic, and structurally unable to import the LLM layer.

`tests/test_llm.py::test_detect_and_eval_import_graphs_exclude_spandan_llm`
enforces that second half in Phase 5.
"""

from .interface import AXES, Detector, DetectorConfig, Flag
from .reference import ReferenceDetector

__all__ = ["AXES", "Detector", "DetectorConfig", "Flag", "ReferenceDetector"]
