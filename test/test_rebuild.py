"""Rebuilding the games corpus from the cached month JSON (IMP-REBUILD)."""

import json

import pytest

from boardviz import db, fetch

# A minimal chess.com-style game blob (one short game) for a cached archive file.
_PGN = (
    '[White "alice"]\n[Black "bob"]\n[Result "1-0"]\n[ECO "C20"]\n'
    '[TimeControl "180"]\n[Termination "alice won by checkmate"]\n\n'
    "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0\n"
)
_GAME = {
    "url": "https://www.chess.com/game/live/g-1",
    "pgn": _PGN,
    "time_control": "180",
    "end_time": 1_700_000_000,
    "uuid": "g-1",
}


class TestRebuildFromArchives:
    """After a DB loss, games are reconstructed from the cached JSON."""

    @pytest.mark.spec("IMP-REBUILD")
    def test_rebuild_restores_games_unanalyzed(self, monkeypatch, tmp_path, conn):
        """records_from_archives + upsert_games repopulates games (analyzed=0)."""
        # Arrange: a cached month file for 'alice' under a temp archives dir.
        monkeypatch.setattr(fetch.config, "ARCHIVES_DIR", tmp_path)
        d = tmp_path / "alice"
        d.mkdir()
        (d / "2025-01.json").write_text(json.dumps({"games": [_GAME]}))
        # Act: reconstruct records from the cache and insert into a fresh DB.
        records = fetch.records_from_archives("alice")
        inserted = db.upsert_games(conn, records, "alice")
        # Assert: the game is back and awaiting (re-)analysis.
        assert inserted == 1
        games = db.query_games(conn, username="alice")
        assert len(games) == 1
        assert games[0]["analyzed"] == 0

    @pytest.mark.spec("IMP-REBUILD")
    def test_rebuild_is_idempotent(self, monkeypatch, tmp_path, conn):
        """Re-running the rebuild inserts nothing new (INSERT OR IGNORE by uuid)."""
        # Arrange.
        monkeypatch.setattr(fetch.config, "ARCHIVES_DIR", tmp_path)
        d = tmp_path / "alice"
        d.mkdir()
        (d / "2025-01.json").write_text(json.dumps({"games": [_GAME]}))
        records = fetch.records_from_archives("alice")
        db.upsert_games(conn, records, "alice")
        # Act: rebuild again from the same cache.
        again = db.upsert_games(conn, fetch.records_from_archives("alice"), "alice")
        # Assert.
        assert again == 0
