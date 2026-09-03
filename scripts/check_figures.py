"""Every figure the documents assert, checked against the build's own output.

Why this exists: an audit found three documents carrying figures from a
superseded run, and no test could have caught it — nothing in the suite read a
number out of a Markdown file. This is that guard. `make check` runs it.

Sources of truth, in order of authority:
  data/metrics.json   - written by `make eval`; scores, confusion, threshold,
                        alerts, per-scenario counts, the seed matrix, triage
  data/manifest.json  - written by `make data`; the realised stream properties
  docs/BENCH.md       - the one dated `make bench` run; README bench figures
                        must match it (consistency, not reproduction)
  spandan.triage.graph.render_mermaid() - the README's triage diagram must be
                        exactly what the declaration renders
  data/baselines.json - written by `make baselines`; the learned-baseline rows
                        in README and FAILURE_MODES section 9
  data/experiment_long_horizon*.json - written by `make experiment`; the Phase E
                        long-horizon figures in FAILURE_MODES section 7

Six passes:
  1. POSITIVE   every asserted figure must equal the corresponding value
  2. DERIVED    ratios and roundings quoted in prose must follow from the data
  3. DIAGRAM    the fenced triage mermaid block equals render_mermaid()
  4. NEGATIVE   superseded figures may not survive outside history sections
  5. BASELINES  every learned-baseline figure, and the hand row must equal the
                harness's own seed matrix. The boosted model's row is checked
                within a stated tolerance, not exactly: measured on ubuntu, its
                figures move in the third decimal (FAILURE_MODES section 9,
                platform note) while the hand and logistic rows do not move at all
  6. EXPERIMENT the long-horizon experiment's figures, beside the frozen
                detector's, from its own harness runs

`--only NAME` runs one pass; `--skip NAME` drops one. The CI figures job skips
the experiment (a separate job builds it) and the experiment job runs only it.
A missing source file fails the pass that needs it; nothing is silently skipped.

What it cannot check, stated so nobody over-reads a PASS: time-to-detection
and the budget frontier are not in metrics.json, so "60/60 episodes" and the
frontier rows are trusted to the make eval log; the bench figures are checked
for consistency between README and BENCH.md, not reproduced.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")),
        ROOT / "python" / "spandan" / "gen" / "ASSUMPTIONS.md",
        ROOT / "python" / "spandan" / "llm" / "TARGET.md"]
PASSES = ("positive", "derived", "diagram", "negative", "baselines", "experiment")


class Check:
    """Shared state for the passes: documents, lazily loaded sources, failures."""

    def __init__(self) -> None:
        self.docs = {p: p.read_text(encoding="utf-8", errors="replace") for p in DOCS if p.exists()}
        self.readme = self.docs[ROOT / "README.md"]
        self.fm = self.docs[ROOT / "docs" / "FAILURE_MODES.md"]
        self.fails: list[str] = []
        self.count = 0
        self._json: dict[str, dict] = {}

    def source(self, name: str) -> dict | None:
        """A JSON source under data/, loaded once; a missing file is a failure."""
        if name not in self._json:
            path = ROOT / "data" / name
            if not path.exists():
                self.fails.append(f"data/{name} is missing; the build step that writes it has not run")
                self._json[name] = None
            else:
                self._json[name] = json.loads(path.read_text(encoding="utf-8"))
        return self._json[name]

    def expect(self, label: str, text: str, token: str, where: str) -> None:
        self.count += 1
        if token not in text:
            self.fails.append(f"{where}: expected {token!r} ({label})")

    def derived(self, label: str, ok: bool, token: str, text: str, where: str = "README") -> None:
        self.count += 1
        if not ok:
            self.fails.append(f"DERIVED {label}: arithmetic does not hold")
        elif token not in text:
            self.fails.append(f"{where}: expected derived phrase {token!r} ({label})")


def spread(rows: list[dict], key: str) -> tuple[float, float, float]:
    vals = [r[key] for r in rows]
    return statistics.median(vals), min(vals), max(vals)


# ---------------------------------------------------------------- 1. POSITIVE
def pass_positive(ck: Check) -> None:
    metrics, manifest = ck.source("metrics.json"), ck.source("manifest.json")
    if metrics is None or manifest is None:
        return
    bench = (ROOT / "docs" / "BENCH.md").read_text(encoding="utf-8")
    c, tri, ps = metrics["confusion"], metrics["triage"], metrics["per_scenario"]
    full = [r for r in metrics["seed_matrix"] if r["variant"] == "full"]

    for label, token in [
        ("precision @ target",   f"{metrics['precision_at_target_prevalence']:.4f}"),
        ("precision",            f"{metrics['precision']:.4f}"),
        ("recall",               f"{metrics['recall']:.4f}"),
        ("PR-AUC",               f"{metrics['pr_auc']:.4f}"),
        ("threshold",            f"{metrics['threshold']:.2f}"),
        ("alerts",               f"{metrics['alerts']}"),
        ("break-even",           f"₹{metrics['break_even_review_rupees']:.0f}"),
        ("flag rate %",          f"{metrics['flag_rate'] * 100:.2f}%"),
        ("legit decline %",      f"{metrics['legit_decline_rate'] * 100:.2f}%"),
        ("1 in N",               f"1 in {round(1 / metrics['legit_decline_rate'])}"),
        ("flagged events",       f"{c['tp'] + c['fp']:,}"),
        ("outage flagged",       f"{ps['outage_single_merchant']['flagged']:,}"),
        ("outage events",        f"{ps['outage_single_merchant']['events']:,}"),
        ("triage trips",         f"{tri['trips']} trips"),
        ("triage 1 in N",        f"1 in {round(1 / tri['legit_decline_rate'])}"),
        ("triage outage alerted", f"{tri['per_scenario']['outage_single_merchant']['alerted']:,}"),
    ]:
        ck.expect(label, ck.readme, token, "README")

    # The 2.1a table in FAILURE_MODES carries the per-count triage figures.
    for label, token in [
        ("triage fp declined",     f"{tri['fp_declined']:,}"),
        ("triage outage declined", f"{tri['per_scenario']['outage_single_merchant']['declined']:,}"),
        ("triage outage alerted",  f"{tri['per_scenario']['outage_single_merchant']['alerted']:,}"),
        ("triage 1 in N",          f"1 in {round(1 / tri['legit_decline_rate'])}"),
        ("triage trips",           f"**{tri['trips']}**"),
    ]:
        ck.expect(label, ck.fm, token, "FAILURE_MODES")

    for label, token in [
        ("multiseed net median", f"₹{statistics.median(r['net_rupees'] for r in full):,.0f}"),
        ("multiseed net min",    f"₹{min(r['net_rupees'] for r in full):,.0f}"),
        ("multiseed net max",    f"₹{max(r['net_rupees'] for r in full):,.0f}"),
        ("multiseed precision",  f"{statistics.median(r['precision'] for r in full):.4f}"),
    ]:
        ck.expect(label, ck.readme, token, "README")
        ck.expect(label, ck.fm, token, "FAILURE_MODES")

    neg = manifest["negative_case"]
    assumptions = ck.docs[ROOT / "python" / "spandan" / "gen" / "ASSUMPTIONS.md"]
    for label, token in [
        ("flash-sale known share",   f"{neg['flash_sale_known_customer_share'] * 100:.1f}%"),
        ("flash-sale new share",     f"{neg['flash_sale_new_customer_share'] * 100:.1f}%"),
        ("outage decline ratio",     f"{neg['issuer_outage']['decline_ratio'] * 100:.1f}%"),
        ("outage attempts/card",     f"{neg['issuer_outage']['attempts_per_card']:.2f}"),
        ("outage known share",       f"{neg['issuer_outage']['known_customer_share'] * 100:.1f}%"),
        ("outage distinct BINs",     f"{neg['issuer_outage']['distinct_bins']}"),
    ]:
        ck.expect(label, assumptions, token, "ASSUMPTIONS")

    # README bench figures must match BENCH.md (consistency).
    for token in re.findall(r"\b\d{1,3}(?:,\d{3})+ events/s|\b\d+\.\d+µs|\b\d,\d{3} bytes|\d+\.\d+ GB|\d\.\d{2}×", ck.readme):
        norm = token.replace("µs", "").replace(" events/s", "").replace(" bytes", "").replace(" GB", "").replace("×", "")
        ck.count += 1
        if norm not in bench:
            ck.fails.append(f"README bench figure {token!r} not found in BENCH.md")


# ----------------------------------------------------------------- 2. DERIVED
def pass_derived(ck: Check) -> None:
    metrics = ck.source("metrics.json")
    if metrics is None:
        return
    c, tri, ps = metrics["confusion"], metrics["triage"], metrics["per_scenario"]
    outage = ps["outage_single_merchant"]
    ck.derived("50.5% outage flagged", abs(outage["flagged"] / outage["events"] * 100 - 50.5) < 0.05, "50.5%", ck.readme)
    ck.derived("42 events per alert", round((c["tp"] + c["fp"]) / metrics["alerts"]) == 42, "42 events per alert", ck.readme)
    ck.derived("31% recovery", abs(tri["per_scenario"]["outage_single_merchant"]["alerted"] / c["fp"] * 100 - 31) < 1.0, "31%", ck.readme)
    ck.derived("TP unchanged by triage", tri["tp_declined"] == tri["tp_raw"] == c["tp"], f"{c['tp']:,} → {c['tp']:,}", ck.readme)
    p = metrics["precision_at_target_prevalence"]
    ck.derived("eleven false alarms", round((1 - p) / p) == 11, "eleven false alarms", ck.readme)


# ----------------------------------------------------------------- 3. DIAGRAM
def pass_diagram(ck: Check) -> None:
    sys.path.insert(0, str(ROOT / "python"))
    from spandan.triage.graph import render_mermaid  # noqa: E402

    blocks = re.findall(r"```mermaid\n(.*?)\n```", ck.readme, re.S)
    ck.count += 1
    if render_mermaid().strip() not in [b.strip() for b in blocks]:
        ck.fails.append("README: the triage mermaid block is not what render_mermaid() produces")


# ---------------------------------------------------------------- 4. NEGATIVE
def pass_negative(ck: Check) -> None:
    # Superseded figures. Allowed only where they are explicitly history.
    history = {"AUDIT.md", "BUILD_LOG.md", "PHASES.md"}
    banned = ["39.9%", "7,240", "1,573", "0.3985", "23.05", "₹8,595", "₹1.79L", "13,947",
              "11,800", "₹2.82L", "₹5.59L", "₹2.91L", "57.5%", "~42%", "~10%", "5.36",
              "89.9%", "₹1,664", "82.4%", "120,053", "24.80", "119.30", "21,766", "3,874",
              "31.0 GB", "2,058 MB"]
    for path, text in ck.docs.items():
        if path.name in history:
            continue
        body = text.split("## 6. These figures moved")[0] if path.name == "BENCH.md" else text
        for fig in banned:
            ck.count += 1
            if fig in body:
                ck.fails.append(f"{path.relative_to(ROOT)}: superseded figure {fig!r} survives")


# --------------------------------------------------------------- 5. BASELINES
#: The boosted model (HistGradientBoosting) is not platform-exact: regenerated
#: on ubuntu it moves precision 0.4427 -> 0.4525, recall 0.9651 -> 0.9599 and
#: the outage count 12,644 -> 12,091 while every hand and logistic figure is
#: identical. Its row is quoted from the dated Windows run and any regeneration
#: must land within these of it. The tolerances are stated in section 9.
#: ratio 0.03: the largest gap measured across every quoted boosted figure was
#: 0.021, on the best-seed recall (0.9998 Windows, 0.9790 ubuntu).
GBM_TOLERANCE = {"ratio": 0.03, "net": 0.02, "count": 0.06, "alerts_per_day": 0.8, "one_in_n": 8}


def _row_numbers(text: str, section: str, prefix: str) -> list[float]:
    """Numbers in the first Markdown table row beginning with `prefix` after
    `section` appears. Thousands separators and ranges are handled; a leading
    minus is kept."""
    body = text[text.index(section):]
    for line in body.splitlines():
        if line.startswith(prefix):
            cells = [c.strip() for c in line.strip("|").split("|")][1:]
            out: list[float] = []
            for cell in cells:
                for m in re.findall(r"-?\d[\d,]*\.?\d*", cell):
                    out.append(float(m.replace(",", "")))
            return out
    return []


def pass_baselines(ck: Check) -> None:
    bl, metrics = ck.source("baselines.json"), ck.source("metrics.json")
    if bl is None or metrics is None:
        return
    sm = bl["seed_matrix"]

    def close(label: str, quoted: float | None, actual: float, tol: float, relative: bool) -> None:
        ck.count += 1
        if quoted is None:
            ck.fails.append(f"FAILURE_MODES/README: could not find the quoted figure for {label}")
            return
        gap = abs(quoted - actual) / (abs(actual) or 1.0) if relative else abs(quoted - actual)
        if gap > tol:
            ck.fails.append(f"BASELINES {label}: quoted {quoted} vs regenerated {actual:.4f}, outside tolerance {tol}")

    for name in ("hand", "logreg6"):
        row = bl["models"][name]
        for label, token in [
            (f"{name} p@base",    f"{row['precision_at_target_prevalence']:.4f}"),
            (f"{name} precision", f"{row['precision']:.4f}"),
            (f"{name} recall",    f"{row['recall']:.4f}"),
            (f"{name} net",       f"{row['net_rupees']:,.0f}"),
            (f"{name} 1 in N",    f"1 in {round(1 / row['legit_decline_rate'])}"),
        ]:
            ck.expect(label, ck.fm, token, "FAILURE_MODES")
        for scenario in ("issuer_outage", "outage_single_merchant"):
            v = row["per_scenario"][scenario]
            ck.expect(f"{name} {scenario}", ck.fm, f"{v['flagged']:,}/{v['events']:,} ({v['rate']:.1%})", "FAILURE_MODES")
        rows = [r for r in sm if r["model"] == name]
        for key, label in (("precision_at_target_prevalence", "p@base"), ("recall", "recall"), ("net_rupees", "net")):
            med, lo, hi = spread(rows, key)
            fmt = (lambda v: f"{v:,.0f}") if key == "net_rupees" else (lambda v: f"{v:.4f}")
            ck.expect(f"{name} median {label}", ck.fm, fmt(med), "FAILURE_MODES")
            ck.expect(f"{name} median {label}", ck.readme, fmt(med), "README")
            ck.expect(f"{name} min {label}", ck.fm, fmt(lo), "FAILURE_MODES")
            ck.expect(f"{name} max {label}", ck.fm, fmt(hi), "FAILURE_MODES")
    for term, w in bl["models"]["logreg6"]["weights"].items():
        ck.expect(f"logreg6 multiplier {term}", ck.fm, f"{w:+.3f}", "FAILURE_MODES")

    # The boosted row, within tolerance of the dated run it is quoted from.
    g = bl["models"]["gbm9"]
    t = GBM_TOLERANCE
    main = _row_numbers(ck.fm, "## 9. Learned weights", "| gbm9 |")
    # cells: threshold, precision, alert precision, p@base, recall, PR-AUC, net, alerts/day, 1 in N
    pick = lambda xs, i: xs[i] if len(xs) > i else None  # noqa: E731
    close("gbm9 precision", pick(main, 1), g["precision"], t["ratio"], False)
    close("gbm9 p@base", pick(main, 3), g["precision_at_target_prevalence"], t["ratio"], False)
    close("gbm9 recall", pick(main, 4), g["recall"], t["ratio"], False)
    close("gbm9 net", pick(main, 6), g["net_rupees"], t["net"], True)
    close("gbm9 alerts/day", pick(main, 7), g["alerts_per_day"], t["alerts_per_day"], False)
    close("gbm9 1 in N", pick(main, 9), round(1 / g["legit_decline_rate"]), t["one_in_n"], False)
    controls = _row_numbers(ck.fm, "Negative controls, events flagged", "| gbm9 |")
    # cells per control: flagged, events, rate%  -> issuer_outage flagged at 3, outage_single flagged at 6
    close("gbm9 issuer_outage flagged", pick(controls, 3), g["per_scenario"]["issuer_outage"]["flagged"], 3.0, False)
    close("gbm9 outage_single flagged", pick(controls, 6), g["per_scenario"]["outage_single_merchant"]["flagged"], t["count"], True)
    rows = [r for r in sm if r["model"] == "gbm9"]
    spread_row = _row_numbers(ck.fm, "**Across three independently generated streams**", "| gbm9 |")
    readme_row = _row_numbers(ck.readme, "**Against learned baselines on the same features**", "| gradient boosting")
    for key, label, i, relative in (
        ("precision_at_target_prevalence", "p@base", 0, False), ("recall", "recall", 3, False), ("net_rupees", "net", 6, True),
    ):
        med, lo, hi = spread(rows, key)
        tol = t["net"] if relative else t["ratio"]
        close(f"gbm9 median {label} (FM)", pick(spread_row, i), med, tol, relative)
        close(f"gbm9 min {label}", pick(spread_row, i + 1), lo, tol, relative)
        close(f"gbm9 max {label}", pick(spread_row, i + 2), hi, tol, relative)
        close(f"gbm9 median {label} (README)", pick(readme_row, i // 3), med, tol, relative)

    # The control: the hand row's spread is the harness's own seed matrix.
    full = [r for r in metrics["seed_matrix"] if r["variant"] == "full"]
    ck.count += 1
    if abs(spread([r for r in sm if r["model"] == "hand"], "net_rupees")[0] - statistics.median(r["net_rupees"] for r in full)) > 0.5:
        ck.fails.append("BASELINES: the hand row's median net does not equal the harness seed matrix")
    outage = bl["models"]["logreg6"]["per_scenario"]["outage_single_merchant"]
    boosted = g["per_scenario"]["outage_single_merchant"]
    ck.derived("45% outage under logreg6", round(outage["flagged"] / outage["events"] * 100) == 45, "45%", ck.readme)
    ck.derived("two in three under gbm9", 0.60 <= boosted["flagged"] / boosted["events"] <= 0.75, "two in three", ck.readme)


# -------------------------------------------------------------- 6. EXPERIMENT
EXPERIMENTS = {"long_horizon": "experiment_long_horizon.json", "long_horizon_x5": "experiment_long_horizon_x5.json"}


def pass_experiment(ck: Check) -> None:
    """The section 7 table: for each registered variant, the seed-zero figures
    and the three-seed spread, beside the frozen detector's own rows from the
    same runs. Figures are quoted in FAILURE_MODES section 7."""
    for variant, filename in EXPERIMENTS.items():
        ex = ck.source(filename)
        if ex is None:
            continue
        ck.count += 1
        if ex.get("variant") != variant:
            ck.fails.append(f"data/{filename}: variant label is {ex.get('variant')!r}, expected {variant!r}")
        outage = ex["per_scenario"]["outage_single_merchant"]
        ck.expect(f"{variant} outage flag rate", ck.readme, f"{outage['flagged'] / outage['events'] * 100:.1f}%", "README")
        for label, token in [
            (f"{variant} outage flag rate", f"{outage['flagged'] / outage['events'] * 100:.1f}%"),
            (f"{variant} outage flagged count", f"{outage['flagged']:,}/{outage['events']:,}"),
            (f"{variant} precision", f"{ex['precision']:.4f}"),
            (f"{variant} PR-AUC", f"{ex['pr_auc']:.4f}"),
            (f"{variant} threshold", f"{ex['threshold']:.2f}"),
            (f"{variant} p@base",           f"{ex['precision_at_target_prevalence']:.4f}"),
            (f"{variant} recall",           f"{ex['recall']:.4f}"),
            (f"{variant} legit decline %",  f"{ex['legit_decline_rate'] * 100:.2f}%"),
            (f"{variant} 1 in N",           f"1 in {round(1 / ex['legit_decline_rate'])}"),
            (f"{variant} alerts/day",       f"{ex['alerts_per_day']:.1f}"),
        ]:
            ck.expect(label, ck.fm, token, "FAILURE_MODES")
        rows = [r for r in ex["seed_matrix"] if r["variant"] == variant]
        frozen = [r for r in ex["seed_matrix"] if r["variant"] == "full"]
        for key, label, fmt in (
            ("precision", "precision", lambda v: f"{v:.4f}"),
            ("recall", "recall", lambda v: f"{v:.4f}"),
            ("net_rupees", "net", lambda v: f"{v:,.0f}"),
        ):
            med, lo, hi = spread(rows, key)
            for what, val in (("median", med), ("min", lo), ("max", hi)):
                ck.expect(f"{variant} {what} {label}", ck.fm, fmt(val), "FAILURE_MODES")
        for scenario in ("burst", "rotating", "slow_low", "issuer_outage"):
            ck.expect(f"{variant} {scenario} rate", ck.fm, f"{ex['per_scenario'][scenario]['rate']:.4f}", "FAILURE_MODES")
        # The frozen rows inside the experiment run must be the frozen detector's
        # own seed matrix, or the comparison is not like for like.
        metrics = ck._json.get("metrics.json")
        if metrics and variant == "long_horizon_x5":
            phrase = f"{(metrics['recall'] - ex['recall']) * 100:.1f} points"
            ck.derived("recall cost of the five-fold weight", True, phrase, ck.fm, "FAILURE_MODES")
            ck.derived("recall cost of the five-fold weight (README)", True, phrase, ck.readme, "README")
        if metrics:
            full = [r for r in metrics["seed_matrix"] if r["variant"] == "full"]
            ck.count += 1
            if [round(r["net_rupees"], 2) for r in frozen] != [round(r["net_rupees"], 2) for r in full]:
                ck.fails.append(f"EXPERIMENT {variant}: the frozen rows in the experiment run differ from make eval")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Every documented figure, checked against the build.")
    parser.add_argument("--only", choices=PASSES, default=None)
    parser.add_argument("--skip", choices=PASSES, action="append", default=[])
    args = parser.parse_args(argv)
    active = [args.only] if args.only else [p for p in PASSES if p not in args.skip]

    ck = Check()
    runners = {
        "positive": pass_positive, "derived": pass_derived, "diagram": pass_diagram,
        "negative": pass_negative, "baselines": pass_baselines, "experiment": pass_experiment,
    }
    # metrics.json is read by several passes; load it first when any of them runs
    # so the experiment pass can cross-check against it if it is present.
    if "experiment" in active and (ROOT / "data" / "metrics.json").exists():
        ck.source("metrics.json")
    for name in active:
        runners[name](ck)

    engine = ck._json.get("metrics.json", {}) or {}
    print(f"passes {', '.join(active)}: checked {ck.count} figures across {len(ck.docs)} documents"
          + (f" (engine in metrics.json: {engine['engine']})" if engine else ""))
    if ck.fails:
        print("FAIL")
        for f in ck.fails:
            print("  -", f)
        return 1
    print("PASS: every asserted figure matches the build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
