"""Shared reduced configs. Small enough to run in a test suite, structurally
identical to the shipped stream: all five scenarios, positives on both sides of
the temporal split, the same reserved identifier ranges."""

from __future__ import annotations

from spandan.gen.config import GenConfig, ScenarioEpisodeSpec

SMALL_EPISODES = (
    ScenarioEpisodeSpec("burst", 0.50, 8.0, 120, 120, 1, 1, 0.86, 100, 5_000, 0),
    ScenarioEpisodeSpec("rotating", 1.50, 20.0, 140, 140, 30, 28, 0.84, 100, 5_000, 1),
    ScenarioEpisodeSpec("slow_low", 2.20, 240.0, 40, 40, 3, 3, 0.72, 100, 3_000, 2),
    ScenarioEpisodeSpec("flash_sale", 1.10, 40.0, 300, 280, 270, 260, 0.16, 20_000, 900_000, 1),
    ScenarioEpisodeSpec("burst", 3.30, 8.0, 110, 110, 1, 1, 0.87, 100, 5_000, 2),
    ScenarioEpisodeSpec("rotating", 3.60, 20.0, 130, 130, 28, 26, 0.85, 100, 5_000, 0),
    ScenarioEpisodeSpec("slow_low", 3.10, 200.0, 36, 36, 3, 3, 0.73, 100, 3_000, 1),
    ScenarioEpisodeSpec("flash_sale", 3.80, 40.0, 280, 260, 250, 240, 0.15, 20_000, 900_000, 2),
    ScenarioEpisodeSpec("issuer_outage", 2.60, 60.0, 400, 130, 125, 120, 0.83, 1_000, 5_000_00, 0, 3),
    ScenarioEpisodeSpec("issuer_outage", 3.45, 55.0, 360, 120, 115, 110, 0.81, 1_000, 5_000_00, 1, 3),
)

SMALL_CONFIG = GenConfig(
    seed=4_242_424,
    total_days=4,
    train_days=3,
    merchant_count=3,
    benign_card_pool_size=1_200,
    benign_ip_pool_size=900,
    benign_device_pool_size=800,
    benign_bin_pool_size=40,
    episodes=SMALL_EPISODES,
)
