"""Phase C: the learned baselines, reported through the detector's own pipeline.

The design claim under test: six hand-weighted terms, summed, are enough. Two
models are fitted on the same features the detector computes and reported
through the same threshold selection, cost model, reweighting and multi-seed
spread, so the comparison is like for like:

  hand      the frozen detector's score, passed through unchanged. This row
            must equal `make eval` field for field; it is the control.
  logreg6   logistic regression on the six terms. Same features, learned
            weights. The cleanest test of the claim.
  gbm9      a small gradient-boosted model on the six terms plus log-amount,
            declined and hour of day. Does richer combination help.

Where the labels come from. The detector needs none. A learned model needs
labelled events that are neither the validation window (which chooses every
threshold, learned or hand) nor the test window. The warm-up window - the
first 75% of the training days - is the only such data, so the models fit
there, are thresholded on validation under the same alerts/day budget as the
detector, and are read on test once. `test_baseline_threshold_selected_on_
validation_only` poisons the test split during fitting and selection.

Nothing here ships. The learned models are reported, not deployed: the
detector is frozen and `spandan.detect` does not import this module or
scikit-learn. scikit-learn is a dev extra for this module alone.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from pathlib import Path

import numpy as np

from ..gen.build import MANIFEST_FILENAME, build
from ..gen.config import default_config
from ..gen.schema import ATTACK_SCENARIOS, NEGATIVE_CONTROLS
from .costs import CostModel
from .features import (
    FEATURE_NAMES,
    TERM_NAMES,
    Features,
    extract_split,
    labels_of,
    load_features,
)
from .harness import evaluate_scored, select_threshold, sweep_thresholds
from .loader import Split, load_split

MODELS = ("hand", "logreg6", "gbm9")
COLUMNS = {"logreg6": slice(0, len(TERM_NAMES)), "gbm9": slice(0, len(FEATURE_NAMES))}
BASELINES_FILENAME = "baselines.json"


class Scorer:
    """A fitted model reduced to one call: features and detector score in,
    ranking score out. For `hand` the ranking score is the detector score."""

    def __init__(self, name: str, estimator=None, columns: slice | None = None):
        self.name = name
        self.estimator = estimator
        self.columns = columns

    def __call__(self, x: np.ndarray, detector_score: np.ndarray) -> np.ndarray:
        if self.name == "hand":
            return detector_score
        return np.asarray(self.estimator.decision_function(x[:, self.columns]), dtype=np.float64)


def fit(name: str, feats: Features, warmup_labels: np.ndarray) -> Scorer:
    """Fit one model on the warm-up window. `hand` fits nothing."""
    if name == "hand":
        return Scorer(name)
    columns = COLUMNS[name]
    x = feats.warmup_x[:, columns]
    if name == "logreg6":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        estimator = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000))
    elif name == "gbm9":
        from sklearn.ensemble import HistGradientBoostingClassifier

        estimator = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.1, max_leaf_nodes=31, early_stopping=False, random_state=0
        )
    else:
        raise ValueError(f"unknown model {name!r}; one of {MODELS}")
    estimator.fit(x, warmup_labels)
    return Scorer(name, estimator, columns)


def learned_weights(scorer: Scorer) -> dict[str, float] | None:
    """For `logreg6`: the coefficient on each term in the units of the term, so
    it reads as a multiplier on the hand weight already inside that term."""
    if scorer.name != "logreg6":
        return None
    scaler = scorer.estimator.named_steps["standardscaler"]
    logistic = scorer.estimator.named_steps["logisticregression"]
    per_unit = logistic.coef_[0] / scaler.scale_
    return {name: float(w) for name, w in zip(TERM_NAMES, per_unit)}


def select_operating_point(
    name: str, split: Split, feats: Features, model: CostModel, budget: float | None
) -> tuple[Scorer, dict]:
    """Fit on warm-up, choose the threshold on validation. Never reads test."""
    scorer = fit(name, feats, labels_of(split.train_warmup))
    validation_scores = scorer(feats.validation_x, feats.validation_score)
    sweep = sweep_thresholds(split.validation, validation_scores, model)
    return scorer, select_threshold(sweep, budget)


def run_models(
    split: Split,
    feats: Features,
    model: CostModel,
    episode_windows: list[dict],
    names: tuple[str, ...] = MODELS,
    budget: float | None = None,
) -> dict[str, dict]:
    budget = model.alerts_per_day_budget if budget is None else budget
    out: dict[str, dict] = {}
    for name in names:
        scorer, _ = select_operating_point(name, split, feats, model, budget)
        validation_scores = scorer(feats.validation_x, feats.validation_score)
        test_scores = scorer(feats.test_x, feats.test_score)
        result = evaluate_scored(split, validation_scores, test_scores, model, episode_windows, budget)
        out[name] = summarise_row(name, result, model)
        out[name]["weights"] = learned_weights(scorer)
    return out


def summarise_row(name: str, result: dict, model: CostModel) -> dict:
    confusion = result["confusion"]
    per_scenario = {
        scenario: {"events": v["events"], "flagged": v["flagged"], "rate": v["rate"]}
        for scenario, v in result["per_scenario"].items()
    }
    return {
        "model": name,
        "threshold": result["threshold"],
        "budget_infeasible": result["threshold_source"]["budget_infeasible"],
        "precision": confusion.precision,
        "alert_precision": result["alert_precision"],
        "precision_at_target_prevalence": result["reweighted"].precision_target,
        "recall": confusion.recall,
        "pr_auc": result["average_precision"],
        "tp": confusion.tp,
        "fp": confusion.fp,
        "fn": confusion.fn,
        "net_rupees": result["costs"].net_paise(model.assumed_review_paise) / 100.0,
        "alerts": len(result["alerts"]),
        "alerts_per_day": result["alerts_per_day"],
        "flag_rate": result["flag_rate"],
        "legit_decline_rate": result["legit_decline_rate"],
        "per_scenario": per_scenario,
    }


def run_seed_matrix(
    seed_count: int,
    base_seed: int,
    model: CostModel,
    names: tuple[str, ...] = MODELS,
    first: tuple[Split, Features, list[dict]] | None = None,
) -> list[dict]:
    """Every model on every seed, the harness's spread applied to the learned
    rows. `first` lets the caller pass the already-extracted base stream so
    seed zero is not regenerated."""
    rows: list[dict] = []
    for offset in range(max(seed_count, 1)):
        seed = base_seed + offset
        if offset == 0 and first is not None:
            split, feats, windows = first
            results = run_models(split, feats, model, windows, names)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                manifest = build(default_config(seed=seed), tmp)
                split = load_split(tmp)
                feats = extract_split(split)
                results = run_models(split, feats, model, manifest["episode_windows"], names)
        for name, row in results.items():
            rows.append({"seed": seed} | {k: v for k, v in row.items() if k not in ("per_scenario", "weights")})
    return rows


def _spread(values: list[float]) -> tuple[float, float, float]:
    return statistics.median(values), min(values), max(values)


def render(results: dict[str, dict], rows: list[dict], model: CostModel, seed_count: int) -> None:
    r = "₹"
    print("=" * 78)
    print("LEARNED BASELINES vs THE HAND-WEIGHTED DETECTOR  (same features, same pipeline)")
    print("=" * 78)
    print("Fitted on the warm-up window; thresholds chosen on validation under the same")
    print(f"alerts/day <= {model.alerts_per_day_budget:g} budget; read once on test. Precision at the")
    print(f"{model.target_prevalence:.2%} base rate leads, as everywhere else in this project.")
    print()
    head = f"{'model':8} {'thr':>9} {'prec':>7} {'alert-p':>8} {'p@base':>7} {'recall':>7} {'PR-AUC':>7} {'net ' + r:>12} {'al/day':>7} {'1 in N':>7}"
    print(head)
    print("-" * len(head))
    for name, row in results.items():
        one_in = round(1 / row["legit_decline_rate"]) if row["legit_decline_rate"] else 0
        print(
            f"{name:8} {row['threshold']:9.3f} {row['precision']:7.4f} {row['alert_precision']:8.3f} "
            f"{row['precision_at_target_prevalence']:7.4f} {row['recall']:7.4f} {row['pr_auc']:7.4f} "
            f"{row['net_rupees']:12,.0f} {row['alerts_per_day']:7.1f} {one_in:7d}"
            + ("  (budget infeasible)" if row["budget_infeasible"] else "")
        )
    print()
    print("Negative controls: events flagged (false positives by construction)")
    print(f"{'model':8} " + " ".join(f"{s[:22]:>22}" for s in NEGATIVE_CONTROLS))
    for name, row in results.items():
        cells = []
        for scenario in NEGATIVE_CONTROLS:
            v = row["per_scenario"].get(scenario)
            cells.append(f"{v['flagged']:,}/{v['events']:,} ({v['rate']:.1%})" if v else "-")
        print(f"{name:8} " + " ".join(f"{c:>22}" for c in cells))
    print()
    print("Attack scenarios: events flagged (recall per scenario)")
    print(f"{'model':8} " + " ".join(f"{s:>12}" for s in ATTACK_SCENARIOS))
    for name, row in results.items():
        cells = []
        for scenario in ATTACK_SCENARIOS:
            v = row["per_scenario"].get(scenario)
            cells.append(f"{v['rate']:.4f}" if v else "-")
        print(f"{name:8} " + " ".join(f"{c:>12}" for c in cells))
    weights = results.get("logreg6", {}).get("weights")
    if weights:
        print()
        print("logreg6 learned multiplier per term (1.0 = the hand weight already inside the term):")
        for term in TERM_NAMES:
            print(f"  {term:16} {weights[term]:+8.3f}")
    print()
    print(f"Spread across {seed_count} independently generated streams (median, min-max):")
    print(f"{'model':8} {'p@base':>24} {'recall':>24} {'net ' + r:>34}")
    for name in results:
        sub = [x for x in rows if x["model"] == name]
        if not sub:
            continue
        p = _spread([x["precision_at_target_prevalence"] for x in sub])
        rc = _spread([x["recall"] for x in sub])
        net = _spread([x["net_rupees"] for x in sub])
        print(
            f"{name:8} {p[0]:8.4f} ({p[1]:.4f}-{p[2]:.4f}) {rc[0]:8.4f} ({rc[1]:.4f}-{rc[2]:.4f}) "
            f"{net[0]:12,.0f} ({net[1]:,.0f}-{net[2]:,.0f})"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Learned baselines against the hand-weighted detector.")
    parser.add_argument("--data", default="data")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--features", default=None, help="reuse an extracted features.npz for the base stream")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    manifest = json.loads((data_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    windows = manifest["episode_windows"]
    model = CostModel.load()
    split = load_split(data_dir)
    feats = load_features(args.features) if args.features else extract_split(split)
    if feats.test_x.shape[0] != len(split.test) or feats.validation_x.shape[0] != len(split.validation):
        raise SystemExit("features.npz does not match the split in --data; re-run spandan.eval.features")

    results = run_models(split, feats, model, windows)
    rows = run_seed_matrix(args.seeds, manifest["seed"], model, first=(split, feats, windows))
    render(results, rows, model, args.seeds)

    if args.json_out:
        payload = {
            "engine": "python reference (feature extraction reads ReferenceDetector._advance)",
            "seed": manifest["seed"],
            "seeds": args.seeds,
            "alerts_per_day_budget": model.alerts_per_day_budget,
            "target_prevalence": model.target_prevalence,
            "fitted_on": "train_warmup",
            "threshold_selected_on": "validation",
            "feature_names": list(FEATURE_NAMES),
            "models": results,
            "seed_matrix": rows,
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
