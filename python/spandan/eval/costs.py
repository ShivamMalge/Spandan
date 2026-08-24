"""The rupee cost model, and the prevalence reweighting.

Two ideas carry this module.

**The manual review cost is an output, not an input.** It is the one parameter
with no defensible source, so making the headline rest on it would make the
headline undefendable. Instead the harness reports the *break-even*: net stays
positive while a review costs under X rupees per alert. The unciteable number
becomes the answer rather than an assumption.

**Precision is reweighted to a stated prevalence.** Recall is prevalence-
independent; precision is not. The generator's positive rate (~1.4%) is far above
a real merchant's card-testing rate, so precision measured on it is an upper
bound. Holding the positives fixed and rescaling the negative class to a target
prevalence gives a precision that can be argued about, instead of one that has to
be apologised for.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..gen.schema import STATUS_APPROVED, Event

COSTS_PATH = Path(__file__).with_name("costs.toml")


@dataclass(frozen=True, slots=True)
class CostModel:
    auth_fee_paise: int
    chargeback_fee_paise: int
    chargeback_loss_fraction: float
    chargeback_rate_on_approved_fraud: float
    contribution_margin: float
    only_charge_if_approved: bool
    assumed_review_paise: int
    target_prevalence: float
    alerts_per_day_budget: float
    frontier_budgets: tuple[float, ...]

    @classmethod
    def load(cls, path: Path | str = COSTS_PATH) -> "CostModel":
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            auth_fee_paise=raw["auth_fee"]["paise_per_blocked_attempt"],
            chargeback_fee_paise=raw["chargeback"]["fee_paise"],
            chargeback_loss_fraction=raw["chargeback"]["loss_fraction_of_amount"],
            chargeback_rate_on_approved_fraud=raw["chargeback"]["rate_on_approved_fraud"],
            contribution_margin=raw["blocked_good"]["contribution_margin"],
            only_charge_if_approved=raw["blocked_good"]["only_charge_if_would_have_been_approved"],
            assumed_review_paise=raw["review"]["assumed_cost_paise_per_alert"],
            target_prevalence=raw["prevalence"]["target_rate"],
            alerts_per_day_budget=raw["operations"]["alerts_per_day_budget"],
            frontier_budgets=tuple(raw["operations"]["frontier_budgets"]),
        )


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    saved_auth_fees_paise: float
    avoided_chargebacks_paise: float
    blocked_good_paise: float
    blocked_good_events: int
    blocked_good_events_that_would_decline: int
    alerts: int
    per_scenario_blocked_good_paise: dict
    per_scenario_flagged: dict

    @property
    def gross_paise(self) -> float:
        """Everything except the review cost, which is the parameter under
        argument. Break-even is computed off this."""
        return (
            self.saved_auth_fees_paise
            + self.avoided_chargebacks_paise
            - self.blocked_good_paise
        )

    def net_paise(self, review_cost_paise: float) -> float:
        return self.gross_paise - self.alerts * review_cost_paise

    def break_even_review_paise(self) -> float:
        """Highest per-alert review cost at which the detector still pays for
        itself. Negative gross means no review cost makes it worthwhile."""
        if self.alerts == 0:
            return math.inf if self.gross_paise > 0 else 0.0
        return self.gross_paise / self.alerts


def compute_costs(
    events: list[Event],
    scores: np.ndarray,
    threshold: float,
    model: CostModel,
    alert_count: int,
) -> CostBreakdown:
    saved_auth = 0.0
    avoided_cb = 0.0
    blocked_good = 0.0
    blocked_good_events = 0
    would_decline = 0
    per_scenario_cost: dict[str, float] = {}
    per_scenario_flagged: dict[str, int] = {}

    for i, event in enumerate(events):
        if scores[i] <= threshold:
            continue
        per_scenario_flagged[event.scenario_id] = per_scenario_flagged.get(event.scenario_id, 0) + 1

        if event.label == 1:
            saved_auth += model.auth_fee_paise
            if event.status == STATUS_APPROVED:
                exposure = (
                    model.chargeback_fee_paise
                    + model.chargeback_loss_fraction * event.amount_paise
                )
                avoided_cb += model.chargeback_rate_on_approved_fraud * exposure
            continue

        # A blocked clean transaction. If it was going to decline anyway, the
        # merchant loses no margin - see costs.toml, blocked_good.approval_basis.
        blocked_good_events += 1
        if model.only_charge_if_approved and event.status != STATUS_APPROVED:
            would_decline += 1
            continue
        cost = model.contribution_margin * event.amount_paise
        blocked_good += cost
        per_scenario_cost[event.scenario_id] = per_scenario_cost.get(event.scenario_id, 0.0) + cost

    return CostBreakdown(
        saved_auth_fees_paise=saved_auth,
        avoided_chargebacks_paise=avoided_cb,
        blocked_good_paise=blocked_good,
        blocked_good_events=blocked_good_events,
        blocked_good_events_that_would_decline=would_decline,
        alerts=alert_count,
        per_scenario_blocked_good_paise=per_scenario_cost,
        per_scenario_flagged=per_scenario_flagged,
    )


# --- prevalence reweighting -------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reweighted:
    observed_prevalence: float
    target_prevalence: float
    negative_scale: float
    precision_observed: float
    precision_target: float
    recall: float
    effective_fp: float


def reweight_to_prevalence(
    tp: int, fp: int, fn: int, tn: int, target_prevalence: float
) -> Reweighted:
    """Hold the positive class fixed; rescale the negative class.

    Positives P = tp + fn are what they are. For the positives to be a
    `target_prevalence` share of the population, the negative class must number
    P * (1 - target) / target. Scaling the observed negatives to that count and
    scaling `fp` with them gives precision at the target rate. Recall is
    untouched, because recall does not depend on how many negatives there are —
    asserted by `test_reweighting_leaves_recall_unchanged`.
    """
    positives = tp + fn
    negatives = fp + tn
    if positives == 0 or negatives == 0 or not (0.0 < target_prevalence < 1.0):
        raise ValueError("reweighting needs both classes present and 0 < target < 1")

    required_negatives = positives * (1.0 - target_prevalence) / target_prevalence
    scale = required_negatives / negatives
    effective_fp = fp * scale

    return Reweighted(
        observed_prevalence=positives / (positives + negatives),
        target_prevalence=target_prevalence,
        negative_scale=scale,
        precision_observed=tp / (tp + fp) if (tp + fp) else 0.0,
        precision_target=tp / (tp + effective_fp) if (tp + effective_fp) else 0.0,
        recall=tp / positives,
        effective_fp=effective_fp,
    )


def gross_at(pre, threshold: float, model: CostModel) -> float:
    """Gross rupee position at a threshold, vectorised. See metrics.SweepPrecompute."""
    import numpy as np

    flagged = pre.scores > threshold
    if not flagged.any():
        return 0.0

    fraud = flagged & (pre.labels == 1)
    clean = flagged & (pre.labels == 0)

    saved_auth = float(fraud.sum()) * model.auth_fee_paise
    approved_fraud = fraud & pre.approved
    exposure = (
        model.chargeback_fee_paise
        + model.chargeback_loss_fraction * pre.amounts[approved_fraud]
    )
    avoided = model.chargeback_rate_on_approved_fraud * float(np.sum(exposure))

    chargeable = clean & pre.approved if model.only_charge_if_approved else clean
    blocked_good = model.contribution_margin * float(np.sum(pre.amounts[chargeable]))

    return saved_auth + avoided - blocked_good
