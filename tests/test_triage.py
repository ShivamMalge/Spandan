"""The triage graph: the post-detection layer, tested as topology and as behaviour.

The load-bearing test is `test_no_path_from_explain_to_act`: the language model
node cannot reach an action, and that is a fact about the declared edges, not a
promise. The rest checks that the audit is written before the action, that the
trail is byte-identical across runs, that the kill-switch trips on the traffic it
was registered for and not on attacks, and that nothing here changes a score.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import sys

import pytest

from spandan.detect.interface import Flag
from spandan.gen.schema import STATUS_APPROVED, STATUS_DECLINED, Event
from spandan.triage import graph as G
from spandan.triage.graph import (
    EDGES,
    END,
    NODES,
    START,
    TriageConfig,
    TriageContext,
    compile_graph,
    reachable_from,
    render_mermaid,
    run,
)


def _event(i: int, *, merchant="mer_001", bin_="099813", card=None, declined=True, ts=None) -> Event:
    return Event(
        ts=ts if ts is not None else 1_784_576_000_000 + i * 1_000,
        txn_id=f"txn_{i:06d}",
        merchant_id=merchant,
        bin=bin_,
        card_ref=card or f"card_{i:010d}",
        ip="192.0.2.5",
        device_id="dev_0000000001",
        amount_paise=545,
        status=STATUS_DECLINED if declined else STATUS_APPROVED,
        label=0,
        scenario_id="test",
    )


def _flag(e: Event, score: float = 24.28) -> Flag:
    return Flag(
        ts=e.ts,
        txn_id=e.txn_id,
        merchant_id=e.merchant_id,
        bin=e.bin,
        score=score,
        threshold=21.99,
        window_events=1,
        window_declines=1,
        window_decline_ratio=1.0,
        baseline_decline_ratio=0.099,
        velocity_z=0.0,
        baseline_window_events=1.1,
        window_distinct_cards=1,
        cards_per_event=1.0,
        window_distinct_merchants=1,
        window_amount_mean_paise=545.0,
        baseline_amount_mean_paise=283_258.0,
        window_saturated=False,
        contributions=(),
    )


def _feed(ctx: TriageContext, events: list[Event]) -> Event:
    for e in events:
        ctx.observe(e)
    return events[-1]


# --- topology --------------------------------------------------------------------


def test_graph_compiles_and_every_node_is_reachable():
    info = compile_graph()
    assert info["start"] == START and info["end"] == END
    assert set(info["nodes"]) == set(NODES)
    assert set(NODES) - reachable_from(START) - {START} == set()


def test_no_path_from_explain_to_act():
    """THE load-bearing test. The LLM node annotates a decision already made
    and recorded; it can never reach the node that executes one."""
    assert "act" not in reachable_from("explain")
    assert "mode" not in reachable_from("explain")
    assert "kill_switch" not in reachable_from("explain")


def test_llm_node_has_exactly_one_outgoing_edge_into_ground():
    targets, _ = EDGES["explain"]
    assert targets == ("ground",)


def test_every_declared_target_is_a_node_or_end():
    for node, (targets, _router) in EDGES.items():
        assert node in NODES
        for t in targets:
            assert t == END or t in NODES


def test_mermaid_is_rendered_from_the_declaration():
    """The diagram cannot drift from the code: every edge in EDGES is in the
    rendering, and no edge in the rendering is absent from EDGES."""
    text = render_mermaid()
    rendered = set()
    for line in text.splitlines():
        if " --> " in line:
            left, right = line.strip().split(" --> ")
            rendered.add((left, right))
    declared = {(n, t) for n, (targets, _) in EDGES.items() for t in targets}
    declared.add(("START([flag])", START))
    assert rendered == declared
    assert "explain --> ground" in text


# --- behaviour -----------------------------------------------------------------


def test_audit_entry_exists_before_act_runs(tmp_path):
    ctx = TriageContext(audit_path=tmp_path / "audit.jsonl")
    cfg = TriageConfig()
    e = _feed(ctx, [_event(1)])
    state = run(e, _flag(e), ctx, cfg)
    assert state.executed and state.decision == "decline"
    nodes_in_order = [entry["node"] for entry in ctx.audit if entry["txn_id"] == e.txn_id]
    assert nodes_in_order.index("mode") < nodes_in_order.index("act")

    # and the `act` node itself refuses without the record
    bare = TriageContext()
    s = G.TriageState(event=e, flag=_flag(e))
    s.decision = "decline"
    with pytest.raises(RuntimeError, match="before its decision was audited"):
        G.act(s, bare, cfg)


def test_audit_trail_is_byte_identical_across_runs(tmp_path):
    def one_run(path):
        ctx = TriageContext(audit_path=path)
        cfg = TriageConfig()
        for i in range(40):
            e = _event(i, card=f"card_{i % 5:010d}")
            ctx.observe(e)
            run(e, _flag(e), ctx, cfg)
        ctx.close()
        return path.read_bytes()

    assert one_run(tmp_path / "a.jsonl") == one_run(tmp_path / "b.jsonl")
    for line in (tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)  # every line is one valid JSON object


def test_kill_switch_trips_on_retry_structure_and_not_on_distinct_cards():
    """Registered basis (costs.toml): outage_single_merchant sits at 3.68-5.41
    attempts per card over an hour on train; attacks at or below 1.65. Build
    both shapes synthetically and check the switch separates them."""
    cfg = TriageConfig(kill_switch_retry_ratio=2.5, kill_switch_min_events=20)

    # An outage: 30 events over 5 cards -> ratio 6.0
    ctx = TriageContext()
    last = _feed(ctx, [_event(i, card=f"card_{i % 5:010d}") for i in range(30)])
    s = run(last, _flag(last), ctx, cfg)
    assert s.tripped and s.tripped_now and s.decision == "alert" and s.mode == "alert_only"
    assert ctx.trips and ctx.trips[0]["retry_ratio"] >= 2.5

    # A burst: 30 events over 30 distinct cards -> ratio 1.0
    ctx2 = TriageContext()
    last2 = _feed(ctx2, [_event(i) for i in range(30)])
    s2 = run(last2, _flag(last2), ctx2, cfg)
    assert not s2.tripped and s2.decision == "decline" and not ctx2.trips

    # Below the minimum-events floor nothing trips, whatever the ratio.
    ctx3 = TriageContext()
    last3 = _feed(ctx3, [_event(i, card="card_0000000001") for i in range(10)])
    assert not run(last3, _flag(last3), ctx3, cfg).tripped


def test_kill_switch_holds_alert_only_for_the_cooldown_then_releases():
    cfg = TriageConfig(kill_switch_retry_ratio=2.5, kill_switch_min_events=20, kill_switch_cooldown_ms=60_000)
    ctx = TriageContext()
    e = _feed(ctx, [_event(i, card=f"card_{i % 5:010d}") for i in range(30)])
    trip = run(e, _flag(e), ctx, cfg)
    assert trip.tripped_now

    # 30s later, still tripped even though this single event would not re-trip
    later = _event(999, ts=e.ts + 30_000)
    ctx.observe(later)
    assert run(later, _flag(later), ctx, cfg).tripped

    # after the cooldown, and with the hot window aged out, it releases
    much_later = _event(1000, ts=e.ts + 2 * G.HOUR_MS)
    ctx.observe(much_later)
    assert not run(much_later, _flag(much_later), ctx, cfg).tripped


def test_alert_only_mode_declines_nothing():
    ctx = TriageContext()
    cfg = TriageConfig(mode="alert_only")
    e = _feed(ctx, [_event(1)])
    s = run(e, _flag(e), ctx, cfg)
    assert s.decision == "alert" and not s.executed and "act" not in s.path


def test_human_review_interrupt_halts_and_persists_state(tmp_path):
    ctx = TriageContext(audit_path=tmp_path / "audit.jsonl")
    cfg = TriageConfig(mode="alert_only")
    e = _feed(ctx, [_event(1)])
    s = run(e, _flag(e), ctx, cfg, interrupt=True)
    assert s.halted and s.path[-1] == "human_review" and "explain" not in s.path
    ctx.close()
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    trail = [json.loads(line) for line in lines]
    assert trail[-1]["node"] == "human_review" and trail[-1]["decision"] == "alert"


def test_dedup_follows_the_alert_cooldown():
    ctx = TriageContext()
    cfg = TriageConfig()
    a = _feed(ctx, [_event(1)])
    sa = run(a, _flag(a), ctx, cfg)
    b = _feed(ctx, [_event(2, ts=a.ts + 60_000)])
    sb = run(b, _flag(b), ctx, cfg)
    c = _feed(ctx, [_event(3, ts=b.ts + cfg.dedup_cooldown_ms + 60_000)])
    sc = run(c, _flag(c), ctx, cfg)
    assert sa.new_alert and not sb.new_alert and sc.new_alert


class _Verdict:
    """A stand-in for the validator's verdict: only `.ok` and `str()` are read."""

    def __init__(self, ok: bool, reason: str = "ok") -> None:
        self.ok = ok
        self.reason = reason

    def __str__(self) -> str:
        return "ok" if self.ok else self.reason


def test_explain_is_optional_and_grounding_gates_the_note():
    cfg = TriageConfig()
    e = _event(1)
    f = _flag(e)

    ctx_none = TriageContext()
    ctx_none.observe(e)
    s = run(e, f, ctx_none, cfg)
    assert s.note is not None and not s.note_grounded and "template" in s.path

    ctx_bad = TriageContext(
        explainer=lambda fl: "Block if CVV mismatched.",
        grounder=lambda n, fl: _Verdict(False, "cites CVV"),
    )
    ctx_bad.observe(e)
    s_bad = run(e, f, ctx_bad, cfg)
    assert not s_bad.note_grounded and "template" in s_bad.path and "CVV" in s_bad.reject_reason
    assert s_bad.decision == "decline" and s_bad.executed  # the decision was untouched by the note

    ctx_ok = TriageContext(
        explainer=lambda fl: "Rs 5.45 declined; 100% vs 10%.",
        grounder=lambda n, fl: _Verdict(True),
    )
    ctx_ok.observe(e)
    s_ok = run(e, f, ctx_ok, cfg)
    assert s_ok.note_grounded and "template" not in s_ok.path and s_ok.note.startswith("Rs 5.45")


def test_triage_never_changes_a_score():
    """Scores in equal scores out: the graph reads the Flag and never writes it."""
    ctx = TriageContext()
    cfg = TriageConfig()
    e = _event(1)
    f = _flag(e, score=24.28)
    ctx.observe(e)
    before = dataclasses.asdict(f)
    run(e, f, ctx, cfg)
    assert dataclasses.asdict(f) == before
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.score = 0.0  # type: ignore[misc]


def test_triage_package_does_not_import_the_llm_layer():
    for name in [m for m in sys.modules if m.startswith("spandan")]:
        del sys.modules[name]
    importlib.import_module("spandan.triage.graph")
    assert not [m for m in sys.modules if m.startswith("spandan.llm")], (
        "the triage graph imported spandan.llm; the explainer must be injected"
    )


def test_no_node_reads_labels_or_scenario_ids():
    """The Event carries `label` and `scenario_id` for the evaluation. The graph
    routes on evidence only: no node function may read either. Asserted on the
    source of every node, since a routing rule keyed on ground truth would be
    invisible in the topology."""
    import inspect

    for name, fn in NODES.items():
        src = inspect.getsource(fn)
        assert ".label" not in src and "scenario_id" not in src, f"node {name!r} reads ground truth"
    for name, (_targets, router) in EDGES.items():
        src = inspect.getsource(router)
        assert ".label" not in src and "scenario_id" not in src, f"router for {name!r} reads ground truth"


def test_config_loads_registered_parameters_from_costs_toml():
    cfg = TriageConfig.load()
    assert cfg.kill_switch_retry_ratio == 2.5
    assert cfg.kill_switch_min_events == 20
    assert cfg.kill_switch_cooldown_ms == 3_600_000
    assert cfg.dedup_cooldown_ms == 15 * 60 * 1000
