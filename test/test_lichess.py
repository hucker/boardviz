"""Lichess PGN import: parse a game stream into GameRecords and upsert (IMP-LICHESS)."""

import chess
import pytest

from boardviz import db, lichess

# Two games as lichess' export API streams them: a checkmate win and a loss on
# time, both for hucker233 (once White, once White again), with %clk + opening.
_PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/abcd1234"]
[White "hucker233"]
[Black "Magnus"]
[Result "1-0"]
[UTCDate "2025.12.25"]
[UTCTime "12:00:00"]
[TimeControl "300+3"]
[ECO "C20"]
[Opening "Italian Game"]
[Termination "Normal"]

1. e4 { [%clk 0:05:00] } e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0

[Event "Rated Bullet game"]
[Site "https://lichess.org/efgh5678"]
[White "hucker233"]
[Black "Nakamura"]
[Result "0-1"]
[UTCDate "2025.12.24"]
[UTCTime "10:00:00"]
[TimeControl "60+0"]
[Termination "Time forfeit"]

1. e4 c5 2. Nf3 d6 0-1

[Event "Rated Chess960 game"]
[Site "https://lichess.org/zzzz9999"]
[White "hucker233"]
[Black "Someone"]
[Result "1-0"]
[Variant "Chess960"]
[TimeControl "300+3"]

1. e4 e5 1-0
"""


class TestLichessImport:
    """The lichess PGN parser and its round-trip through upsert_games."""

    @pytest.mark.spec("IMP-LICHESS")
    def test_records_from_pgn_resolves_pov_termination_and_flags(self):
        """POV, outcome, url/uuid, and lichess-specific termination all resolve."""
        recs = lichess.records_from_pgn(_PGN, "hucker233")
        assert len(recs) == 2  # the Chess960 game is skipped (standard chess only)
        win, loss = recs
        # Checkmate win — termination inferred from the final position ("Normal").
        assert win.my_color == chess.WHITE
        assert (win.outcome, win.flagged) == ("win", False)
        assert win.termination == "checkmate"
        assert win.url == "https://lichess.org/abcd1234" and win.uuid == "abcd1234"
        assert win.time_control == "300+3"
        # Loss on time — flagged, and the wording normalised for end-method.
        assert (loss.outcome, loss.flagged) == ("loss", True)
        assert loss.termination == "won on time"
        # Username match is case-insensitive.
        assert lichess.records_from_pgn(_PGN, "HUCKER233")[0].my_color == chess.WHITE

    @pytest.mark.spec("IMP-LICHESS")
    def test_upsert_derives_tc_opening_and_end_method(self, conn):
        """Upserting lichess records fills tc_class, opening and end_method the
        same way chess.com records do (the pipeline is source-agnostic)."""
        assert db.upsert_games(conn, lichess.records_from_pgn(_PGN, "hucker233"),
                               "hucker233", source="lichess") == 2
        rows = {r["url"]: r for r in conn.execute(
            "SELECT url, tc_class, opening, end_method, flagged, source FROM games")}
        g1 = rows["https://lichess.org/abcd1234"]
        assert g1["tc_class"] == "blitz" and g1["opening"] == "Italian Game"
        assert g1["end_method"] == "checkmate" and g1["source"] == "lichess"
        g2 = rows["https://lichess.org/efgh5678"]
        assert (g2["tc_class"], g2["end_method"], g2["flagged"]) == ("bullet", "on time", 1)
