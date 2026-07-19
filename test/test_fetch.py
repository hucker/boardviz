"""Import helpers: time-control classification, archive walking, PGN parsing."""

import datetime as dt

import pytest

from chesstrain import config, db, fetch


class TestTimeControl:
    """Mapping a chess.com time-control string to a class (bullet/blitz/...)."""

    @pytest.mark.spec("IMP-TC")
    def test_tc_class_boundaries(self):
        """Base seconds map to the right class at each boundary."""
        assert config.tc_class("60") == "bullet"
        assert config.tc_class("180") == "blitz"
        assert config.tc_class("300+2") == "blitz"
        assert config.tc_class("600") == "rapid"
        assert config.tc_class("1/86400") == "daily"

    @pytest.mark.spec("IMP-TC")
    def test_tc_class_handles_untimed_and_empty(self):
        """chess.com's "-" (untimed) and empty strings don't crash."""
        assert config.base_seconds("-") is None
        assert config.tc_class("-") == "daily"
        assert config.tc_class("") == "daily"


class TestArchiveWalk:
    """Enumerating and parsing the monthly archive URLs a fetch walks."""

    @pytest.mark.spec("IMP-FETCH")
    def test_months_between_is_inclusive(self):
        """The month range includes both endpoints."""
        got = list(fetch.months_between(dt.date(2025, 11, 1), dt.date(2026, 2, 15)))
        assert got == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]

    @pytest.mark.spec("IMP-FETCH")
    def test_archive_url_year_month_is_parsed(self):
        """A monthly-archive URL yields its (year, month)."""
        url = "https://api.chess.com/pub/player/x/games/2026/07"
        assert fetch._archive_year_month(url) == (2026, 7)


class TestGameParsing:
    """Parsing a fetched game into player-POV metadata."""

    @pytest.mark.spec("IMP-FETCH")
    def test_load_games_classifies_pov_and_result(self, records):
        """A parsed game resolves colour/outcome to the player and keeps clocks."""
        # Arrange: the single Scholar's-mate game as "alice" (White).
        (rec,) = records
        # Assert.
        assert rec.my_color is True     # alice = White
        assert rec.outcome == "win"
        assert rec.uuid == "g-1"
        assert "%clk" in rec.pgn        # clocks preserved for later analysis


class TestScoutImport:
    """Importing a user in scout mode files them as an opponent, not as me."""

    @pytest.mark.spec("IMP-SCOUT")
    def test_scout_import_stores_the_user_as_an_opponent(
            self, monkeypatch, conn, records):
        """import_user_games(is_me=False) marks the player and games is_me=0."""
        # Arrange: stub the network fetch and parse so only the upsert runs.
        monkeypatch.setattr(fetch, "fetch_until_n", lambda *a, **k: [{}])
        monkeypatch.setattr(fetch, "_records_from_raw", lambda user, raw: records)
        # Act: import "rival" as a scouted opponent.
        fetch.import_user_games(conn, "rival", 5, is_me=False)
        # Assert: the player row and the games are both opponent-side.
        player = conn.execute(
            "SELECT is_me FROM players WHERE username='rival'").fetchone()
        assert player["is_me"] == 0
        games = db.query_games(conn, username="rival")
        assert games
        assert all(g["is_me"] == 0 for g in games)
