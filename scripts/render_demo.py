"""Render the README demo from real command output.

Nothing here is typed by hand. `docs/img/src/*.txt` are transcripts captured
from the CLI and the harness on the committed data (`spandan replay`, the
engine-swap diff of two `make eval` metrics files, `spandan explain` on the
Rs 150 flash-sale false positive, the operating-point frontier from
`make eval`). This script draws them into:

  docs/img/demo.gif       three scenes, as a terminal recording
  docs/img/frontier.png   the operating-point frontier table
  docs/img/flag_card.png  one flag with all six score contributions, read
                          off the Flag the reference detector returns for
                          txn_000804993 on the committed stream

Re-run after any change to the transcripts: `python scripts/render_demo.py`.
The flag card needs `data/` (make data) and `data/metrics.json` (make eval)
because it warms the detector on the training window before reading the flag,
exactly as the CLI does.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "img" / "src"
OUT = ROOT / "docs" / "img"

FONTS = [
    Path("C:/Windows/Fonts/CascadiaMono.ttf"),
    Path("C:/Windows/Fonts/consola.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
]
FONT_SIZE = 15
LINE_H = 22
PAD = 18
COLS = 104
ROWS = 27
W = PAD * 2 + COLS * 8 + 8
H = PAD * 2 + ROWS * LINE_H

BG = (22, 24, 30)
FG = (222, 225, 230)
DIM = (130, 136, 148)
PROMPT = (118, 200, 142)
FLAG = (255, 198, 92)
RED = (255, 122, 122)
GREEN = (120, 222, 142)


def load_font() -> ImageFont.FreeTypeFont:
    for path in FONTS:
        if path.exists():
            return ImageFont.truetype(str(path), FONT_SIZE)
    raise SystemExit("no monospace font found; add one to FONTS")


FONT = load_font()
CHAR_W = FONT.getlength("M")


def colour_for(line: str) -> tuple[int, int, int]:
    s = line.strip()
    if s.startswith("$ "):
        return PROMPT
    if s.startswith("[FLAG]") or "REPLAY SUMMARY" in s:
        return FLAG
    if s.startswith("[rejected]") or s.startswith("<") or "REJECTED" in s or "CLEAN - false positive" in s:
        return RED
    if s.startswith(">") or s.endswith(" accepted") or "exposure prevented" in s and "running" not in s:
        return GREEN
    if s.startswith("#") or set(s) <= {"=", "-"}:
        return DIM
    return FG


def wrap(line: str, cols: int) -> list[str]:
    if len(line) <= cols:
        return [line]
    return textwrap.wrap(line, cols, replace_whitespace=False, drop_whitespace=False, break_long_words=True) or [""]


class Terminal:
    def __init__(self, cols: int = COLS, rows: int = ROWS) -> None:
        self.cols, self.rows = cols, rows
        self.width = PAD * 2 + int(cols * CHAR_W) + 8
        self.height = PAD * 2 + rows * LINE_H
        self.lines: list[str] = []

    def clear(self) -> None:
        self.lines = []

    def write(self, line: str) -> None:
        for piece in wrap(line.rstrip("\n"), self.cols):
            self.lines.append(piece)

    def render(self) -> Image.Image:
        img = Image.new("RGB", (self.width, self.height), BG)
        draw = ImageDraw.Draw(img)
        visible = self.lines[-self.rows:]
        y = PAD
        for line in visible:
            draw.text((PAD, y), line, font=FONT, fill=colour_for(line))
            y += LINE_H
        return img


def still(lines: list[str], path: Path, cols: int) -> None:
    term = Terminal(cols=cols, rows=len(lines) + 1)
    for line in lines:
        term.write(line)
    term.render().save(path, optimize=True)
    print(f"wrote {path.relative_to(ROOT)}  ({term.width}x{term.height})")


def scene(frames: list[tuple[Image.Image, int]], term: Terminal, prompt: str, body: list[str],
          per_line_ms: int, hold_ms: int, tail: list[str] | None = None) -> None:
    term.clear()
    term.write(f"$ {prompt}")
    frames.append((term.render(), 900))
    for line in body:
        term.write(line)
        frames.append((term.render(), per_line_ms))
    for line in tail or []:
        term.write(line)
        frames.append((term.render(), 600))
    frames.append((term.render(), hold_ms))


def read(name: str) -> list[str]:
    return (SRC / name).read_text(encoding="utf-8").splitlines()


def demo_gif() -> None:
    term = Terminal()
    frames: list[tuple[Image.Image, int]] = []

    scene(frames, term, "spandan replay --data data --limit 20000", read("replay.txt"),
          per_line_ms=110, hold_ms=3200)

    diff_lines = read("engine_diff.txt")
    scene(frames, term,
          "make eval ENGINE=python --json-out python.json && make eval ENGINE=rust --json-out rust.json",
          ["# ... two full evaluations over 1.6M events, one per engine ...", "$ diff python.json rust.json"] + diff_lines,
          per_line_ms=500, hold_ms=3400,
          tail=["# one differing line, the engine label: two engines, one set of numbers"])

    scene(frames, term, "spandan explain --flag-id txn_000806675", read("explain_rejected.txt"),
          per_line_ms=95, hold_ms=4200,
          tail=["# exit code 4: the model note named evidence the pipeline does not have;", "# the deterministic template shipped instead. No number passed through the model."])

    images = [f.quantize(colors=64, method=Image.Quantize.MEDIANCUT) for f, _ in frames]
    durations = [d for _, d in frames]
    out = OUT / "demo.gif"
    images[0].save(out, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True)
    total = sum(durations) / 1000
    print(f"wrote {out.relative_to(ROOT)}  {len(images)} frames, {total:.0f}s, {out.stat().st_size / 1e6:.2f} MB")


def flag_card_lines() -> list[str]:
    """One flag, every contribution, from the detector itself."""
    sys.path.insert(0, str(ROOT / "python"))
    from spandan.detect import DetectorConfig, ReferenceDetector
    from spandan.gen.build import TEST_FILENAME, TRAIN_FILENAME, read_stream

    threshold = json.loads((ROOT / "data" / "metrics.json").read_text(encoding="utf-8"))["threshold"]
    detector = ReferenceDetector(DetectorConfig(threshold=threshold))
    for event in read_stream(ROOT / "data" / TRAIN_FILENAME):
        detector.update(event)
    flag = None
    for event in read_stream(ROOT / "data" / TEST_FILENAME):
        result = detector.update(event)
        if event.txn_id == "txn_000804993":
            flag = result
            break
    assert flag is not None, "txn_000804993 did not flag; is data/ the committed stream?"

    lines = [
        f"FLAG {flag.txn_id}   {flag.merchant_id}   BIN {flag.bin}   score {flag.score:.2f} > threshold {flag.threshold:.2f}",
        "",
        f"  window {flag.window_events} event(s), {flag.window_declines} declined "
        f"({flag.window_decline_ratio:.0%}; this BIN's baseline {flag.baseline_decline_ratio:.0%})",
        f"  mean amount Rs {flag.window_amount_mean_paise / 100:,.2f} vs baseline Rs {flag.baseline_amount_mean_paise / 100:,.0f}",
        f"  velocity {flag.velocity_z:.1f} sd above a baseline of {flag.baseline_window_events:.1f} events/window",
        f"  {flag.window_distinct_cards} distinct card(s), {flag.cards_per_event:.2f} cards/event; "
        f"{flag.window_distinct_merchants} merchant(s); ring saturated: {flag.window_saturated}",
        "",
        "  contribution        weight x term   bar (each # = 1.0 score unit)",
        "  " + "-" * 70,
    ]
    for name, value in flag.contributions:
        bar = "#" * int(round(abs(value)))
        sign = "+" if value >= 0 else "-"
        lines.append(f"  {name:18} {value:+8.2f}   {sign}{bar}")
    lines += [
        "  " + "-" * 70,
        f"  {'sum = score':18} {sum(v for _, v in flag.contributions):+8.2f}",
        "",
        "  the six terms are the whole score; there is no seventh input and no model in this path",
    ]
    return lines


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    demo_gif()
    still(["$ make eval    # the operating-point frontier, from the run the README quotes", ""] + read("frontier.txt"),
          OUT / "frontier.png", cols=150)
    still(flag_card_lines(), OUT / "flag_card.png", cols=96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
