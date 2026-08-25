"""spandan_core — the deterministic Rust detector core.

The compiled extension is built as the private submodule `spandan_core._native`
(maturin's mixed rust/python layout requires the extension to live inside a
package directory under `python-source`). This module re-exports its surface so
callers import `spandan_core` and never `_native` directly.

Explicit re-exports by name, never a star-import: `help(spandan_core.X)` passes
under a star-import while `__all__`-based checks and completion quietly do not
(PHASES.md, Phase 4 in-scope list).

The Python-facing detector API lands in Phase 4; until then the extension
exports only its version.
"""

from ._native import __version__  # noqa: F401

__all__ = ["__version__"]
