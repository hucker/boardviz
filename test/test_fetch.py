"""Fetch helpers and PGN parsing (no network)."""

import datetime as dt

from chesstrain import config, fetch


def test_tc_class_boundaries():
    assert config.tc_class("60") == "bullet"
    assert config.tc_class("180") == "blitz"
    assert config.tc_class("300+2") == "blitz"
    assert config.tc_class("600") == "rapid"
    assert config.tc_class("1/86400") == "daily"


def test_tc_class_handles_untimed_and_empty():
    # chess.com uses "-" for untimed games; must not crash.
    assert config.base_seconds("-") is None
    assert config.tc_class("-") == "daily"
    assert config.tc_class("") == "daily"


def test_months_between_inclusive():
    got = list(fetch.months_between(dt.date(2025, 11, 1), dt.date(2026, 2, 15)))
    assert got == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_archive_year_month():
    url = "https://api.chess.com/pub/player/x/games/2026/07"
    assert fetch._archive_year_month(url) == (2026, 7)


def test_load_games_classifies_pov_and_result(records):
    (rec,) = records
    assert rec.my_color is True          # alice = White
    assert rec.outcome == "win"
    assert rec.uuid == "g-1"
    assert "%clk" in rec.pgn             # clocks preserved for later analysis
