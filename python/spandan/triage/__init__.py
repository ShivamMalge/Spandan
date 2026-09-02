"""The triage graph: what happens after a flag, declared explicitly.

Nodes are plain functions, edges are routing functions over typed state, the
graph is validated at import, and the diagram is rendered from the declaration.
The language model is one leaf node with exactly one outgoing edge into a
validator, and no path from it reaches an action — asserted on the topology.

This package never imports `spandan.llm`. The explainer and validator are
injected by the caller (the CLI wires the real ones; the evaluation wires none),
so the evaluation can run this graph with the LLM package unimportable.
"""

from .graph import (
    EDGES,
    END,
    START,
    TriageConfig,
    TriageContext,
    TriageState,
    compile_graph,
    render_mermaid,
    run,
)

__all__ = [
    "EDGES",
    "END",
    "START",
    "TriageConfig",
    "TriageContext",
    "TriageState",
    "compile_graph",
    "render_mermaid",
    "run",
]
