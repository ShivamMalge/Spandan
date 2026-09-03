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

Four passes:
  1. POSITIVE  every asserted figure must equal the corresponding value
  2. DERIVED   ratios and roundings quoted in prose must follow from the data
  3. DIAGRAM   the fenced triage mermaid block equals render_mermaid()
  4. NEGATIVE  superseded figures may not survive outside history sections
  5. BASELINES every learned-baseline figure, and the hand row must equal the
               harness's own seed matrix

What it cannot check, stated so nobody over-reads a PASS: time-to-detection
and the budget frontier are not in metrics.json, so "60/60 episodes" and the
frontier rows are trusted to the make eval log; the bench figures are checked
for consistency between README and BENCH.md, not reproduced.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")),
        ROOT / "python" / "spandan" / "gen" / "ASSUMPTIONS.md",
        ROOT / "python" / "spandan" / "llm" / "TARGET.md"]


def load() -> tuple[dict, dict, str, dict[Path, str]]:
    metrics = json.loads((ROOT / "data" / "metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    bench = (ROOT / "docs" / "BENCH.md").read_text(encoding="utf-8")
    docs = {p: p.read_text(encoding="utf-8", errors="replace") for p in DOCS if p.exists()}
    return metrics, manifest, bench, docs


def main() -> int:
    metrics, manifest, bench, docs = load()
    readme = docs[ROOT / "README.md"]
    fm = docs[ROOT / "docs" / "FAILURE_MODES.md"]
    fails: list[str] = []

    def expect(label: str, text: str, token: str, where: str) -> None:
        if token not in text:
            fails.append(f"{where}: expected {token!r} ({label})")

    # ------------------------------------------------------------ 1. POSITIVE
    c = metrics["confusion"]
    tri = metrics["triage"]
    ps = metrics["per_scenario"]
    full = [r for r in metrics["seed_matrix"] if r["variant"] == "full"]

    checks = [
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
        ("triage outage alerted",  f"{tri['per_scenario']['outage_single_merchant']['alerted']:,}"),
    ]
    for label, token in checks:
        expect(label, readme, token, "README")

    # The 2.1a table in FAILURE_MODES carries the per-count triage figures.
    for label, token in [
        ("triage fp declined",     f"{tri['fp_declined']:,}"),
        ("triage outage declined", f"{tri['per_scenario']['outage_single_merchant']['declined']:,}"),
        ("triage outage alerted",  f"{tri['per_scenario']['outage_single_merchant']['alerted']:,}"),
        ("triage 1 in N",          f"1 in {round(1 / tri['legit_decline_rate'])}"),
        ("triage trips",           f"**{tri['trips']}**"),
    ]:
        expect(label, fm, token, "FAILURE_MODES")

    for label, token in [
        ("multiseed net median", f"₹{statistics.median(r['net_rupees'] for r in full):,.0f}"),
        ("multiseed net min",    f"₹{min(r['net_rupees'] for r in full):,.0f}"),
        ("multiseed net max",    f"₹{max(r['net_rupees'] for r in full):,.0f}"),
        ("multiseed precision",  f"{statistics.median(r['precision'] for r in full):.4f}"),
    ]:
        expect(label, readme, token, "README")
        expect(label, fm, token, "FAILURE_MODES")

    neg = manifest["negative_case"]
    assumptions = docs[ROOT / "python" / "spandan" / "gen" / "ASSUMPTIONS.md"]
    for label, token in [
        ("flash-sale known share",   f"{neg['flash_sale_known_customer_share'] * 100:.1f}%"),
        ("flash-sale new share",     f"{neg['flash_sale_new_customer_share'] * 100:.1f}%"),
        ("outage decline ratio",     f"{neg['issuer_outage']['decline_ratio'] * 100:.1f}%"),
        ("outage attempts/card",     f"{neg['issuer_outage']['attempts_per_card']:.2f}"),
        ("outage known share",       f"{neg['issuer_outage']['known_customer_share'] * 100:.1f}%"),
        ("outage distinct BINs",     f"{neg['issuer_outage']['distinct_bins']}"),
    ]:
        expect(label, assumptions, token, "ASSUMPTIONS")

    # README bench figures must match BENCH.md (consistency).
    for token in re.findall(r"\b\d{1,3}(?:,\d{3})+ events/s|\b\d+\.\d+µs|\b\d,\d{3} bytes|\d+\.\d+ GB|\d\.\d{2}×", readme):
        norm = token.replace("µs", "").replace(" events/s", "").replace(" bytes", "").replace(" GB", "").replace("×", "")
        if norm not in bench:
            fails.append(f"README bench figure {token!r} not found in BENCH.md")

    # ------------------------------------------------------------ 2. DERIVED
    derived = [
        ("50.5% outage flagged",  abs(ps['outage_single_merchant']['flagged'] / ps['outage_single_merchant']['events'] * 100 - 50.5) < 0.05, "50.5%"),
        ("42 events per alert",   round((c['tp'] + c['fp']) / metrics['alerts']) == 42, "42 events per alert"),
        ("31% recovery",          abs(tri['per_scenario']['outage_single_merchant']['alerted'] / c['fp'] * 100 - 31) < 1.0, "31%"),
        ("TP unchanged by triage", tri['tp_declined'] == tri['tp_raw'] == c['tp'], f"{c['tp']:,} → {c['tp']:,}"),
        ("eleven false alarms",   round((1 - metrics['precision_at_target_prevalence']) / metrics['precision_at_target_prevalence']) == 11, "eleven false alarms"),
    ]
    for label, ok, token in derived:
        if not ok:
            fails.append(f"DERIVED {label}: arithmetic does not hold")
        elif token not in readme:
            fails.append(f"README: expected derived phrase {token!r} ({label})")

    # ------------------------------------------------------------ 3. DIAGRAM
    sys.path.insert(0, str(ROOT / "python"))
    from spandan.triage.graph import render_mermaid  # noqa: E402

    blocks = re.findall(r"```mermaid\n(.*?)\n```", readme, re.S)
    if render_mermaid().strip() not in [b.strip() for b in blocks]:
        fails.append("README: the triage mermaid block is not what render_mermaid() produces")

    # ------------------------------------------------------------ 4. NEGATIVE
    # Superseded figures. Allowed only where they are explicitly history.
    history = {"AUDIT.md", "BUILD_LOG.md", "PHASES.md"}
    banned = ["39.9%", "7,240", "1,573", "0.3985", "23.05", "₹8,595", "₹1.79L", "13,947",
              "11,800", "₹2.82L", "₹5.59L", "₹2.91L", "57.5%", "~42%", "~10%", "5.36",
              "89.9%", "₹1,664", "82.4%", "120,053", "24.80", "119.30", "21,766", "3,874",
              "31.0 GB", "2,058 MB"]
    for path, text in docs.items():
        if path.name in history:
            continue
        body = text.split("## 6. These figures moved")[0] if path.name == "BENCH.md" else text
        for fig in banned:
            if fig in body:
                fails.append(f"{path.relative_to(ROOT)}: superseded figure {fig!r} survives")

    # ------------------------------------------------------------ 5. BASELINES
    baselines_path = ROOT / "data" / "baselines.json"
    n_baselines = 0
    if not baselines_path.exists():
        fails.append("data/baselines.json is missing; run `make baselines`")
    else:
        bl = json.loads(baselines_path.read_text(encoding="utf-8"))
        sm = bl["seed_matrix"]

        def spread(name: str, key: str) -> tuple[float, float, float]:
            vals = [r[key] for r in sm if r["model"] == name]
            return statistics.median(vals), min(vals), max(vals)

        for name in ("hand", "logreg6", "gbm9"):
            row = bl["models"][name]
            for label, token, where, text in [
                (f"{name} p@base",    f"{row['precision_at_target_prevalence']:.4f}", "FAILURE_MODES", fm),
                (f"{name} precision", f"{row['precision']:.4f}", "FAILURE_MODES", fm),
                (f"{name} recall",    f"{row['recall']:.4f}", "FAILURE_MODES", fm),
                (f"{name} net",       f"{row['net_rupees']:,.0f}", "FAILURE_MODES", fm),
                (f"{name} 1 in N",    f"1 in {round(1 / row['legit_decline_rate'])}", "FAILURE_MODES", fm),
            ]:
                expect(label, text, token, where)
                n_baselines += 1
            for scenario in ("issuer_outage", "outage_single_merchant"):
                v = row["per_scenario"][scenario]
                expect(f"{name} {scenario}", fm, f"{v['flagged']:,}/{v['events']:,} ({v['rate']:.1%})", "FAILURE_MODES")
                n_baselines += 1
            for key, label in (("precision_at_target_prevalence", "p@base"), ("recall", "recall"), ("net_rupees", "net")):
                med, lo, hi = spread(name, key)
                fmt = (lambda v: f"{v:,.0f}") if key == "net_rupees" else (lambda v: f"{v:.4f}")
                for text, where in ((fm, "FAILURE_MODES"), (readme, "README")):
                    expect(f"{name} median {label}", text, fmt(med), where)
                    n_baselines += 1
                expect(f"{name} min {label}", fm, fmt(lo), "FAILURE_MODES")
                expect(f"{name} max {label}", fm, fmt(hi), "FAILURE_MODES")
                n_baselines += 2
        for term, w in bl["models"]["logreg6"]["weights"].items():
            expect(f"logreg6 multiplier {term}", fm, f"{w:+.3f}", "FAILURE_MODES")
            n_baselines += 1
        # The control: the hand row's spread is the harness's own seed matrix.
        hand_net = spread("hand", "net_rupees")
        harness_net = [r["net_rupees"] for r in full]
        if abs(hand_net[0] - statistics.median(harness_net)) > 0.5:
            fails.append("BASELINES: the hand row's median net does not equal the harness seed matrix")
        outage = bl["models"]["logreg6"]["per_scenario"]["outage_single_merchant"]
        boosted = bl["models"]["gbm9"]["per_scenario"]["outage_single_merchant"]
        for label, ok, token in [
            ("45% outage under logreg6", round(outage["flagged"] / outage["events"] * 100) == 45, "45%"),
            ("70% outage under gbm9",    round(boosted["flagged"] / boosted["events"] * 100) == 70, "70%"),
        ]:
            if not ok:
                fails.append(f"DERIVED {label}: arithmetic does not hold")
            elif token not in readme:
                fails.append(f"README: expected derived phrase {token!r} ({label})")
            n_baselines += 1

    print(f"checked {len(checks) + 4 + 6 + len(derived) + n_baselines} figures across {len(docs)} documents "
          f"(engine in metrics.json: {metrics['engine']})")
    if fails:
        print("FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: every asserted figure matches the build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
