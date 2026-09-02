"""Schema-grounded validation of an explanation.

The fabrication finding (`docs/FAILURE_MODES.md` §8): the recorded model
conditioned its next action on a CVV/AVS result, per-card history and a
cardholder IP — none of which exist anywhere in this pipeline. The prompt had
said "the evidence below is everything known". It fabricated anyway. Prompt
discipline is not a boundary; this module is the boundary for the prose.

The rule is the simplest one that catches what was recorded: **a note may cite
nothing the model was not shown.** Two checks implement it.

1. A deny-list of evidence this pipeline does not carry. A note mentioning any
   of it is either asserting a fact from a field that does not exist or
   conditioning an action on data the analyst does not have. Either way the
   analyst cannot act on it, so the note fails.
2. Every rupee amount and every percentage in the note must appear in the
   prompt, within rounding. Those are the evidence numbers; a note that
   invents one is inventing evidence.

Validation is against the *rendered prompt*, not the `Flag`, on purpose: the
prompt is exactly what the model saw, and a committed cassette carries its own
prompt, so it can be judged from its file alone. `spandan validate-cassettes`
prints one verdict per committed cassette.

What this does not do, stated so nobody over-reads it: it cannot tell a wrong
inference from a right one. A note that reasons badly from real evidence
passes. It catches the failure class that was actually observed — invented
evidence — and nothing more.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

#: Evidence this pipeline does not carry. Word-boundary, case-insensitive.
#: Each entry is a regex. The list is the schema's complement, not a style
#: guide: everything here is a field or record the `Flag` and the stream do
#: not have, per `gen/ASSUMPTIONS.md` §2.6 and `detect/interface.py`.
FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("CVV/CVC result",         r"\bcv[vc]2?\b"),
    ("AVS result",             r"\bavs\b|\baddress verification\b"),
    ("3-D Secure outcome",     r"\b3-?ds\b|\b3-?d secure\b|\bthree-?d secure\b"),
    ("decline reason code",    r"\b(?:reason|decline|response) codes?\b|\bdo not hono?u?r\b"),
    ("per-card history",       r"\b(?:transaction|prior|purchase|card|cardholder|account|payment) history\b|\bprior transactions\b"),
    ("IP address",             r"\bip address(?:es)?\b|\bcardholder(?:'s)? ip\b|\bsource ip\b"),
    ("device identity",        r"\bdevice (?:fingerprint|id|identifier)\b"),
    ("geography",              r"\bgeo-?location\b|\bgeography\b|\bcountry\b|\bbilling (?:address|country)\b"),
    ("merchant category",      r"\bmcc\b|\bmerchant category\b"),
    ("dispute history",        r"\b(?:chargeback|dispute) history\b"),
)

_RUPEES = re.compile(r"(?:Rs\.?|₹|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)
_PERCENT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...]

    def __str__(self) -> str:
        return "ok" if self.ok else "; ".join(self.reasons)


def _amounts(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _RUPEES.findall(text)]


def _percents(text: str) -> list[float]:
    return [float(m) for m in _PERCENT.findall(text)]


def _close_amount(value: float, allowed: list[float]) -> bool:
    return any(abs(value - a) <= 0.5 or (a > 0 and abs(value - a) / a <= 0.10) for a in allowed)


def _close_percent(value: float, allowed: list[float]) -> bool:
    return any(abs(value - a) <= 1.0 for a in allowed)


def validate(note: str, prompt: str) -> Verdict:
    """Does `note` cite anything the model was not shown in `prompt`?

    Two strings in, a verdict out. No `Flag`, no labels, no state, no network.
    """
    reasons: list[str] = []

    for label, pattern in FORBIDDEN:
        if re.search(pattern, note, flags=re.IGNORECASE):
            reasons.append(f"cites {label}, which this pipeline does not have")

    prompt_amounts = _amounts(prompt)
    for value in _amounts(note):
        if not _close_amount(value, prompt_amounts):
            reasons.append(f"amount Rs {value:g} does not appear in the evidence")

    prompt_percents = _percents(prompt)
    for value in _percents(note):
        if not _close_percent(value, prompt_percents):
            reasons.append(f"figure {value:g}% does not appear in the evidence")

    return Verdict(ok=not reasons, reasons=tuple(reasons))


def validate_cassette(path: Path) -> tuple[str, str, Verdict]:
    """Judge a committed cassette from its own file: (key, model, verdict)."""
    cassette = json.loads(path.read_text(encoding="utf-8"))
    return cassette["key"], cassette["model"], validate(cassette["response_text"], cassette["prompt"])


def cassette_report(directory: Path) -> tuple[str, int, int]:
    """One verdict per cassette under `directory`: (text, rejected, total)."""
    paths = sorted(directory.glob("*.json"))
    if not paths:
        return f"no cassettes under {directory}", 0, 0

    lines = [f"{'cassette':34} {'model':24} verdict"]
    rejected = 0
    for path in paths:
        key, model, verdict = validate_cassette(path)
        rejected += not verdict.ok
        lines.append(f"{key:34} {model:24} {'REJECTED' if not verdict.ok else 'accepted'}")
        lines.extend(f"{'':34} {'':24}   - {reason}" for reason in verdict.reasons)
    lines.append(f"\n{rejected} of {len(paths)} rejected")
    return "\n".join(lines), rejected, len(paths)
