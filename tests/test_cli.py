"""The CLI's two demo-facing behaviours, tested (the external audit found none).

`replay` must refuse the config placeholder threshold rather than flag a fifth
of all traffic on a machine that has not run `make eval`; with an explicit
threshold it runs and reports.
"""

from __future__ import annotations

import pytest

from helpers import SMALL_CONFIG  # noqa: E402
from spandan import cli
from spandan.gen.build import build


@pytest.fixture(scope="module")
def small_data(tmp_path_factory):
    out = tmp_path_factory.mktemp("clistream")
    build(SMALL_CONFIG, out)
    return out


def test_replay_refuses_the_placeholder_threshold(small_data, capsys):
    """No metrics.json and no --threshold: exit 2 with a message, no replay."""
    code = cli.replay(["--data", str(small_data), "--limit", "200", "--quiet"])
    err = capsys.readouterr().err
    assert code == 2
    assert "make eval" in err and "--threshold" in err
    assert "placeholder" in err


def test_replay_runs_with_an_explicit_threshold(small_data, capsys):
    code = cli.replay(["--data", str(small_data), "--limit", "300", "--threshold", "21.99", "--quiet"])
    out = capsys.readouterr().out
    assert code == 0
    assert "REPLAY SUMMARY" in out
    assert "events replayed" in out
