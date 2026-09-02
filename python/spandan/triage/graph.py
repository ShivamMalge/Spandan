"""The triage graph — the post-detection layer, as an explicit graph.

Why a graph and not a function: so the routing is inspectable, the diagram is
derived from the code rather than drawn beside it, and the one property that
matters most — **the language model cannot reach an action** — is a fact about
the topology that a test can assert, not a promise in a docstring.

The shape:

    START -> dedup -> exposure -> kill_switch -> mode -+-> act ----------+-> explain -> ground -+-> END
                                                        +-> human_review -+                     +-> template -> END

Every node is a plain function `(state, ctx, cfg) -> state`. Every edge is a
routing function `state -> next node name`, declared with the full set of names
it may return, so `compile_graph()` can check reachability, termination, and
the LLM isolation statically. Decisions are made and recorded by `mode`, before
`act`; `explain` runs strictly after the action and can only annotate it.

Determinism: timestamps come from the event stream, never the wall clock; the
audit is written with sorted keys and fixed float formatting; two runs over the
same stream produce byte-identical `audit.jsonl`.

What this package does NOT do, stated so nobody over-reads it: it does not
change a score. `test_triage_never_changes_a_score` asserts scores in equal
scores out. It decides what a flag becomes — a decline, an alert, or a hold —
and it says why, in writing, before it does it.
"""

from __future__ import annotations

import json
import tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..detect.interface import Flag
from ..eval.metrics import ALERT_COOLDOWN_MS
from ..gen.schema import STATUS_APPROVED, Event

START = "dedup"
END = "END"
HOUR_MS = 3_600_000
COSTS_PATH = Path(__file__).resolve().parents[1] / "eval" / "costs.toml"


# --- configuration ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TriageConfig:
    """Routing parameters. Loaded from `costs.toml [operations]`, where each
    carries its basis; none is a detector parameter."""

    mode: str = "inline"
    """`inline`: a flag declines the transaction. `alert_only`: a flag opens an
    alert for a human and declines nothing. The shippable configuration in
    FAILURE_MODES 0.1 is alert_only at budget 2."""

    dedup_cooldown_ms: int = ALERT_COOLDOWN_MS
    kill_switch_retry_ratio: float = 2.5
    kill_switch_min_events: int = 20
    kill_switch_cooldown_ms: int = HOUR_MS

    # Cost parameters the `exposure` node needs to price rupee-at-risk.
    auth_fee_paise: int = 150
    chargeback_fee_paise: int = 50_000
    chargeback_loss_fraction: float = 1.0
    chargeback_rate_on_approved_fraud: float = 0.8

    @classmethod
    def load(cls, mode: str = "inline", path: Path | str = COSTS_PATH) -> "TriageConfig":
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        ops = raw["operations"]
        return cls(
            mode=mode,
            kill_switch_retry_ratio=float(ops["kill_switch_retry_ratio"]),
            kill_switch_min_events=int(ops["kill_switch_min_events"]),
            kill_switch_cooldown_ms=int(ops["kill_switch_cooldown_ms"]),
            auth_fee_paise=raw["auth_fee"]["paise_per_blocked_attempt"],
            chargeback_fee_paise=raw["chargeback"]["fee_paise"],
            chargeback_loss_fraction=raw["chargeback"]["loss_fraction_of_amount"],
            chargeback_rate_on_approved_fraud=raw["chargeback"]["rate_on_approved_fraud"],
        )


# --- shared state across a stream ----------------------------------------------


class TriageContext:
    """Everything that persists across flags: the trailing-hour windows the
    kill-switch reads, dedup timestamps, trips, the audit sink, and the
    injected explainer/validator (absent in the evaluation by design)."""

    def __init__(
        self,
        audit_path: Path | str | None = None,
        explainer: Callable[[Flag], str] | None = None,
        grounder: Callable[[str, Flag], object] | None = None,
    ) -> None:
        self._windows: dict[tuple[str, str], deque] = {}
        self._cards: dict[tuple[str, str], dict[str, int]] = {}
        self.last_alert_ts: dict[tuple[str, str], int] = {}
        self.tripped_until: dict[tuple[str, str], int] = {}
        self.trips: list[dict] = []
        self.audit: list[dict] = []
        self._recorded: set[tuple[str, str]] = set()   # (txn_id, node) -> O(1) has_recorded
        self._audit_file = open(audit_path, "w", encoding="utf-8") if audit_path else None
        self.explainer = explainer
        self.grounder = grounder

    # Every event on the stream, flagged or not: the retry ratio is a property
    # of all traffic on a (merchant, BIN), not of the flagged subset.
    def observe(self, event: Event) -> None:
        key = (event.merchant_id, event.bin)
        window = self._windows.setdefault(key, deque())
        cards = self._cards.setdefault(key, {})
        window.append((event.ts, event.card_ref))
        cards[event.card_ref] = cards.get(event.card_ref, 0) + 1
        while window and event.ts - window[0][0] > HOUR_MS:
            _, old_card = window.popleft()
            cards[old_card] -= 1
            if cards[old_card] == 0:
                del cards[old_card]

    def trailing_hour(self, key: tuple[str, str]) -> tuple[int, int]:
        window = self._windows.get(key)
        if not window:
            return 0, 0
        return len(window), len(self._cards[key])

    def write(self, entry: dict) -> None:
        self.audit.append(entry)
        self._recorded.add((entry["txn_id"], entry["node"]))
        if self._audit_file is not None:
            self._audit_file.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            self._audit_file.flush()

    def has_recorded(self, txn_id: str, node: str) -> bool:
        return (txn_id, node) in self._recorded

    def close(self) -> None:
        if self._audit_file is not None:
            self._audit_file.close()
            self._audit_file = None


# --- per-flag state --------------------------------------------------------------


@dataclass(slots=True)
class TriageState:
    event: Event
    flag: Flag
    new_alert: bool = False
    exposure_paise: float = 0.0
    window_events: int = 0
    window_cards: int = 0
    retry_ratio: float = 0.0
    tripped: bool = False
    tripped_now: bool = False
    mode: str = ""
    decision: str = ""
    executed: bool = False
    note: str | None = None
    note_grounded: bool = False
    reject_reason: str = ""
    halted: bool = False
    path: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.event.merchant_id, self.event.bin)


# --- nodes: plain functions ----------------------------------------------------


def dedup(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """Same rule as `metrics.alerts`: one alert per (merchant, BIN) run, closed
    after a cooldown of quiet. Decides whether this flag opens a NEW alert; it
    does not decide whether the transaction declines."""
    last = ctx.last_alert_ts.get(state.key)
    state.new_alert = last is None or (state.event.ts - last) > cfg.dedup_cooldown_ms
    ctx.last_alert_ts[state.key] = state.event.ts
    return state


def exposure(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """Rupee at risk if this flag is real, priced label-free with the cost
    model's own arithmetic: an approved attempt carries chargeback exposure, a
    declined one only the authorization fee."""
    if state.event.status == STATUS_APPROVED:
        raw = cfg.chargeback_fee_paise + cfg.chargeback_loss_fraction * state.event.amount_paise
        state.exposure_paise = cfg.chargeback_rate_on_approved_fraud * raw
    else:
        state.exposure_paise = float(cfg.auth_fee_paise)
    return state


def kill_switch(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """Degrade to alert-only for this (merchant, BIN) when the trailing hour
    looks like an issuer outage rather than a probe run — retries of the same
    cards, which FAILURE_MODES 2.2 shows is invisible at five minutes and
    present at sixty. Parameters and their train-only basis: costs.toml."""
    events, cards = ctx.trailing_hour(state.key)
    state.window_events, state.window_cards = events, cards
    state.retry_ratio = round(events / cards, 4) if cards else 0.0

    until = ctx.tripped_until.get(state.key, -1)
    state.tripped = state.event.ts < until
    if (
        not state.tripped
        and events >= cfg.kill_switch_min_events
        and state.retry_ratio >= cfg.kill_switch_retry_ratio
    ):
        state.tripped = state.tripped_now = True
        ctx.tripped_until[state.key] = state.event.ts + cfg.kill_switch_cooldown_ms
        ctx.trips.append({
            "txn_id": state.event.txn_id, "ts": state.event.ts,
            "merchant_id": state.event.merchant_id, "bin": state.event.bin,
            "window_events": events, "window_cards": cards, "retry_ratio": state.retry_ratio,
        })
    return state


def mode(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """The decision. Made here, recorded here, and not revisited by anything
    downstream — in particular not by the language model."""
    state.mode = "alert_only" if (cfg.mode == "alert_only" or state.tripped) else "inline"
    state.decision = "decline" if state.mode == "inline" else "alert"
    return state


def act(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """Execute the decline. Refuses to run unless the decision is already on
    the audit trail — the audit is written before the action, not after."""
    if not ctx.has_recorded(state.event.txn_id, "mode"):
        raise RuntimeError("act reached before its decision was audited")
    state.executed = True
    return state


def human_review(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """The interrupt. In alert-only mode the flag becomes an item for a person;
    nothing is declined. With `interrupt=True` the run halts here with its
    state persisted, which is what a queue would resume from."""
    state.decision = "alert" if state.new_alert else "alert_dedup"
    return state


def explain(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """The one LLM node. Downstream of every decision; its only successor is
    the validator. Absent an injected explainer (the evaluation), it does
    nothing and says so."""
    if ctx.explainer is None:
        state.note = None
        state.reject_reason = "no explainer wired"
        return state
    state.note = ctx.explainer(state.flag)
    return state


def ground(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """The validator. A note that cites evidence outside the prompt does not
    reach the analyst."""
    if state.note is None or ctx.grounder is None:
        state.note_grounded = False
        state.reject_reason = state.reject_reason or "no validator wired"
        return state
    verdict = ctx.grounder(state.note, state.flag)
    state.note_grounded = bool(getattr(verdict, "ok", False))
    if not state.note_grounded:
        state.reject_reason = str(verdict)
    return state


def template(state: TriageState, ctx: TriageContext, cfg: TriageConfig) -> TriageState:
    """Deterministic fallback from Flag fields only. Cannot fabricate: it can
    only substitute fields that exist."""
    f = state.flag
    state.note = (
        f"Rs {f.window_amount_mean_paise / 100:,.2f} on BIN {f.bin} at {f.merchant_id}: "
        f"{f.window_events} event(s), {f.window_decline_ratio:.0%} declined "
        f"(baseline {f.baseline_decline_ratio:.0%}). Decision: {state.decision}."
    )
    return state


NODES: dict[str, Callable[[TriageState, TriageContext, TriageConfig], TriageState]] = {
    "dedup": dedup,
    "exposure": exposure,
    "kill_switch": kill_switch,
    "mode": mode,
    "act": act,
    "human_review": human_review,
    "explain": explain,
    "ground": ground,
    "template": template,
}


# --- edges: (possible targets, routing function) ---------------------------------


def _route_mode(s: TriageState) -> str:
    return "act" if s.decision == "decline" else "human_review"


def _route_review(s: TriageState) -> str:
    return END if s.halted else "explain"


def _route_ground(s: TriageState) -> str:
    return END if s.note_grounded else "template"


EDGES: dict[str, tuple[tuple[str, ...], Callable[[TriageState], str]]] = {
    "dedup":        (("exposure",),            lambda s: "exposure"),
    "exposure":     (("kill_switch",),         lambda s: "kill_switch"),
    "kill_switch":  (("mode",),                lambda s: "mode"),
    "mode":         (("act", "human_review"),  _route_mode),
    "act":          (("explain",),             lambda s: "explain"),
    "human_review": (("explain", END),         _route_review),
    "explain":      (("ground",),              lambda s: "ground"),
    "ground":       ((END, "template"),        _route_ground),
    "template":     ((END,),                   lambda s: END),
}


# --- compile: the properties, checked -------------------------------------------


def _successors(node: str) -> tuple[str, ...]:
    return EDGES[node][0]


def reachable_from(node: str) -> set[str]:
    seen, stack = set(), [node]
    while stack:
        n = stack.pop()
        for t in _successors(n) if n in EDGES else ():
            if t not in seen and t != END:
                seen.add(t)
                stack.append(t)
    return seen


def compile_graph() -> dict:
    """Validate the declaration. Raises on the first violated property."""
    for node, (targets, _) in EDGES.items():
        assert node in NODES, f"edge from unknown node {node!r}"
        for t in targets:
            assert t == END or t in NODES, f"edge {node!r} -> unknown target {t!r}"
    assert START in NODES
    unreachable = set(NODES) - reachable_from(START) - {START}
    assert not unreachable, f"unreachable nodes: {sorted(unreachable)}"
    for node in NODES:
        # every node must be able to reach END
        assert END in {t for n in reachable_from(node) | {node} for t in _successors(n)}, (
            f"{node!r} cannot reach END"
        )
    # The property that matters: the LLM node annotates, it never decides.
    assert "act" not in reachable_from("explain"), "a path exists from explain to act"
    assert "mode" not in reachable_from("explain"), "a path exists from explain to mode"
    assert _successors("explain") == ("ground",), "explain must have exactly one successor: ground"
    return {"nodes": sorted(NODES), "start": START, "end": END}


# --- run ---------------------------------------------------------------------------


def run(
    event: Event,
    flag: Flag,
    ctx: TriageContext,
    cfg: TriageConfig,
    interrupt: bool = False,
) -> TriageState:
    """Walk one flag through the graph. Every transition is audited before the
    next node runs; `act` checks that its decision is already on the trail."""
    state = TriageState(event=event, flag=flag)
    node = START
    while node != END:
        state.path.append(node)
        state = NODES[node](state, ctx, cfg)
        if node == "human_review" and interrupt:
            state.halted = True
        ctx.write({
            "txn_id": event.txn_id, "ts": event.ts, "node": node,
            "merchant_id": event.merchant_id, "bin": event.bin,
            "decision": state.decision, "mode": state.mode,
            "new_alert": state.new_alert, "tripped": state.tripped, "tripped_now": state.tripped_now,
            "retry_ratio": state.retry_ratio, "window_events": state.window_events,
            "exposure_paise": round(state.exposure_paise, 2),
            "note_grounded": state.note_grounded, "reject_reason": state.reject_reason,
            "score": round(flag.score, 6), "threshold": round(flag.threshold, 6),
        })
        node = EDGES[node][1](state)
    return state


# --- render: the diagram from the declaration -----------------------------------


_LABELS = {
    "dedup": "dedup<br/>15-min cooldown",
    "exposure": "exposure<br/>rupee at risk",
    "kill_switch": "kill_switch<br/>retry ratio / hour",
    "mode": "<b>mode</b><br/>the decision",
    "act": "<b>act</b><br/>decline",
    "human_review": "human_review<br/>alert / hold",
    "explain": "explain<br/><b>the one LLM node</b>",
    "ground": "ground<br/>validator",
    "template": "template<br/>fallback",
}


def render_mermaid() -> str:
    """The diagram, derived from EDGES so it cannot drift from the code.
    `test_mermaid_is_rendered_from_the_declaration` checks the edge set."""
    lines = [
        '%%{init: {"flowchart": {"wrappingWidth": 200, "nodeSpacing": 28, "rankSpacing": 44, "curve": "basis"}}}%%',
        "flowchart LR",
        f"    START([flag]) --> {START}",
    ]
    for node in NODES:
        lines.append(f'    {node}["{_LABELS[node]}"]')
    lines.append("    END([end])")
    for node, (targets, _) in EDGES.items():
        for t in targets:
            lines.append(f"    {node} --> {t}")
    lines += [
        "    classDef plain fill:#4a4f57,color:#fff,stroke:#4a4f57",
        "    classDef llm fill:#3c3f45,color:#fff,stroke:#9aa0a6,stroke-width:2px",
        "    classDef decide fill:#a4262c,color:#fff,stroke:#a4262c",
        "    classDef guard fill:#1f4e79,color:#fff,stroke:#1f4e79",
        "    classDef term fill:#2f6b3a,color:#fff,stroke:#2f6b3a",
        "    class dedup,exposure,human_review,template plain",
        "    class explain llm",
        "    class mode,act decide",
        "    class ground,kill_switch guard",
        "    class START,END term",
    ]
    return "\n".join(lines)


# Validated on import: a graph that violates its own properties never loads.
compile_graph()
