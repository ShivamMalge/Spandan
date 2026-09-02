"""The bounded LLM explanation layer.

One task (`explain_flag`), one provider, one egress point (`provider.complete`),
cassette replay by default. Nothing in `spandan.detect` or `spandan.eval` may
import this package - enforced by tests, not by convention: the import-graph
test walks their imports, and the poisoned-import test proves the evaluation's
numbers survive this package being unimportable.
"""

from .explain import ExplanationRejected, explain_flag, render_prompt, render_template
from .provider import CassetteMiss, complete
from .grounding import Verdict, validate

__all__ = [
    "CassetteMiss",
    "ExplanationRejected",
    "Verdict",
    "complete",
    "explain_flag",
    "render_prompt",
    "render_template",
    "validate",
]
