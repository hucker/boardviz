"""Import helpers: time-control classification, archive walking, PGN parsing."""

import datetime as dt
import json

import pytest

from boardviz import config, db, fetch
from boardviz.blitz_analysis import load_games


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
        assert rec.my_color is True  # alice = White
        assert rec.outcome == "win"
        assert rec.uuid == "g-1"
        assert "%clk" in rec.pgn  # clocks preserved for later analysis

    @pytest.mark.spec("IMP-FETCH")
    def test_load_games_skips_non_standard_variants(self, tmp_path):
        """Chess960 (and other non-'chess' rules) are skipped — analysis assumes
        standard chess."""
        std = {"url": "u1", "uuid": "s1", "time_control": "180",
               "pgn": '[White "a"]\n[Result "1-0"]\n\n1. e4 e5 1-0'}
        v960 = {**std, "uuid": "v1", "rules": "chess960"}
        path = tmp_path / "g.json"
        path.write_text(json.dumps({"games": [std, v960]}))
        recs = load_games(path, username="a", time_control=None)
        assert len(recs) == 1 and recs[0].uuid == "s1"


class TestProfileImport:
    """Every imported user is a profile; the first becomes the default."""

    def _stub(self, monkeypatch, records, tmp_path):
        monkeypatch.setattr(fetch.config, "ARCHIVES_DIR", tmp_path)  # empty -> no prune
        monkeypatch.setattr(fetch, "fetch_until_n", lambda *a, **k: [{}])
        monkeypatch.setattr(fetch, "_records_from_raw", lambda user, raw: records)

    @pytest.mark.spec("IMP-DEFAULT")
    def test_first_import_becomes_the_default_profile(
        self, monkeypatch, conn, records, tmp_path
    ):
        """With no profiles yet, the first import is made the default."""
        # Arrange.
        self._stub(monkeypatch, records, tmp_path)
        # Act.
        fetch.import_user_games(conn, "alice", 5)
        # Assert.
        assert db.default_profile(conn) == "alice"
        assert db.query_games(conn, username="alice")

    @pytest.mark.spec("IMP-DEFAULT")
    def test_default_flag_repoints_the_default(
        self, monkeypatch, conn, records, tmp_path
    ):
        """Importing with default=True re-points the default to that profile."""
        # Arrange: alice imported first (auto-default).
        self._stub(monkeypatch, records, tmp_path)
        fetch.import_user_games(conn, "alice", 5)
        # Act: import bob as the new default.
        fetch.import_user_games(conn, "bob", 5, default=True)
        # Assert.
        assert db.default_profile(conn) == "bob"


class TestPruneArchives:
    """The cached month JSON is capped per profile (IMP-RAWCACHE)."""

    @pytest.mark.spec("IMP-RAWCACHE")
    def test_prune_keeps_the_newest_files_and_spares_merged(
        self, monkeypatch, tmp_path
    ):
        """Older month files are deleted; _merged.json is never pruned."""
        # Arrange: four month files plus the merged blob.
        monkeypatch.setattr(fetch.config, "ARCHIVES_DIR", tmp_path)
        d = tmp_path / "alice"
        d.mkdir()
        for ym in ("2025-01", "2025-02", "2025-03", "2025-04"):
            (d / f"{ym}.json").write_text("{}")
        (d / "_merged.json").write_text("{}")
        # Act: keep only the two newest.
        deleted = fetch.prune_archives("alice", keep=2)
        # Assert.
        assert deleted == ["2025-01.json", "2025-02.json"]
        assert sorted(p.name for p in d.glob("*.json")) == [
            "2025-03.json",
            "2025-04.json",
            "_merged.json",
        ]
