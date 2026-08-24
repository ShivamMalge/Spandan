"""spandan_core — the deterministic Rust detector core.

The compiled extension is built as the private submodule `spandan_core._native`
(maturin's mixed rust/python layout requires the extension to live inside a
package directory under `python-source`). This module re-exports its surface so
callers import `spandan_core` and never `_native` directly.
"""

from ._native import __version__, _smoke_add  # noqa: F401

__all__ = ["__version__", "_smoke_add"]
