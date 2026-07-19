"""Pure classifiers from the analysis pass, and an end-to-end golden-engine run."""

from pathlib import Path

import chess
import pytest

from chesstrain import analysis_batch as ab
from chesstrain import config, db, engine
from chesstrain.blitz_analysis import load_games

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_games.json"


class TestClassifiers:
    """Position key and the structure/move-type/phase tags used to cluster."""

    @pytest.mark.spec("TRN-UNIQ")
    def test_position_key_is_the_epd(self):
        """A position's key is its EPD (layout only), so it recurs across games."""
        board = chess.Board()
        assert ab.position_key(board) == board.epd()

    @pytest.mark.spec("REV-CLUST")
    def test_classify_move_type_ranks_capture_check_over_quiet(self):
        """A move is tagged capture/check/quiet by priority."""
        # Arrange: opening move, a capture, and a queen sortie.
        start = chess.Board()
        after_e5 = chess.Board(
            "rnbqkbnr/pppp1ppp/8/4p3/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2")
        pre_qh5 = chess.Board(
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
        # Act + Assert.
        assert ab.classify_move_type(start, chess.Move.from_uci("e2e4")) == "quiet"
        assert ab.classify_move_type(after_e5, chess.Move.from_uci("d4e5")) == "capture"
        assert ab.classify_move_type(pre_qh5, chess.Move.from_uci("d1h5")) == "quiet"

    @pytest.mark.spec("REV-CLUST")
    def test_classify_move_type_detects_a_retreat(self):
        """A non-pawn moving backward is a retreat."""
        # Arrange: white knight f3 -> g1 (backward for White).
        board = chess.Board(
            "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 1")
        # Act + Assert.
        assert ab.classify_move_type(board, chess.Move.from_uci("f3g1")) == "retreat"

    @pytest.mark.spec("REV-CLUST")
    def test_phase_of_splits_opening_middlegame_endgame(self):
        """Phase is opening (early), endgame (few pieces), else middlegame."""
        assert ab.phase_of(chess.Board(), 1) == "opening"
        assert ab.phase_of(chess.Board(), 20) == "middlegame"
        end = chess.Board("8/5k2/8/8/8/8/3K1P2/8 w - - 0 40")  # K+P vs K
        assert ab.phase_of(end, 40) == "endgame"


class TestGameState:
    """Winning/equal/losing labelling that drives the big-think analytic."""

    @pytest.mark.spec("REV-THINK")
    def test_game_state_thresholds(self):
        """Eval maps to winning/losing past the threshold, else equal."""
        assert ab.game_state(300) == "winning"
        assert ab.game_state(-300) == "losing"
        assert ab.game_state(0) == "equal"


@pytest.fixture(scope="module")
def analyzed_conn():
    """Import the golden fixture and run real Stockfish analysis over it, once.

    Skips (rather than fails) when no engine is available, so machines without
    the vendored Stockfish still pass. Single-threaded for reproducibility —
    multi-threaded search is timing-dependent and jitters evals near a boundary.
    """
    try:
        config.resolve_engine_path()
    except Exception as exc:  # no vendored/configured Stockfish here
        pytest.skip(f"no Stockfish engine available: {exc}")
    conn = db.connect(":memory:")
    db.init_db(conn)
    records = load_games(_FIXTURE, username="alice", time_control=None)
    db.upsert_games(conn, records, "alice", is_me=True)
    eng = engine.open_engine(threads=1)
    try:
        for row in db.unanalyzed_games(conn, "alice"):
            ab.analyze_game(conn, row, eng)
    finally:
        eng.quit()
    yield conn
    conn.close()


def _my_mistakes(conn, uuid: str) -> list:
    """The tracked player's confirmed mistakes in a game, by ply."""
    return conn.execute(
        "SELECT m.* FROM mistakes m JOIN games g ON g.id = m.game_id "
        "WHERE g.game_uuid = ? AND m.is_me = 1 ORDER BY m.ply", (uuid,)).fetchall()


def _move_count(conn, uuid: str) -> int:
    """How many per-move rows were persisted for a game."""
    return conn.execute(
        "SELECT COUNT(*) FROM moves m JOIN games g ON g.id = m.game_id "
        "WHERE g.game_uuid = ?", (uuid,)).fetchone()[0]


class TestGoldenAnalysis:
    """Real Stockfish over a tiny curated fixture yields known results (IMP-ANLZ).

    Asserts robust, engine-version-tolerant facts (a known blunder is flagged for
    the right side with a large drop and a unique answer; a clean win is clean) —
    never exact centipawns or a boundary-straddling end state.
    """

    @pytest.mark.spec("IMP-ANLZ")
    def test_every_game_is_analyzed_with_per_move_rows(self, analyzed_conn):
        """Both games flip to analysed and persist one row per ply."""
        assert len(db.query_games(analyzed_conn, analyzed=1)) == 2
        assert _move_count(analyzed_conn, "golden-1") == 7
        assert _move_count(analyzed_conn, "golden-2") == 8

    @pytest.mark.spec("IMP-ANLZ")
    def test_missed_tactic_is_flagged_as_my_mistake(self, analyzed_conn):
        """Missing the free queen (3.Nc3, not Nxh4) is my one big flagged mistake."""
        # Act.
        mine = _my_mistakes(analyzed_conn, "golden-2")
        # Assert: exactly one, at the Nc3 move, a large drop.
        assert len(mine) == 1
        assert mine[0]["played_uci"] == "b1c3"
        assert mine[0]["phase"] == "opening"
        assert mine[0]["drop_cp"] >= 200   # ~queen; exact cp is engine-dependent

    @pytest.mark.spec("IMP-ANLZ")
    def test_my_mistake_is_cached_as_a_trainer_puzzle(self, analyzed_conn):
        """The mistake seeds the engine-free grade cache; best move = take the queen."""
        # Act.
        best = {r["best_uci"] for r in
                analyzed_conn.execute("SELECT best_uci FROM grades_cache")}
        # Assert: Nxh4 (f3h4) is the cached best answer.
        assert "f3h4" in best

    @pytest.mark.spec("IMP-ANLZ")
    def test_clean_win_has_no_mistake_of_mine_and_snapshots_the_win(self, analyzed_conn):
        """The Scholar's-mate win flags none of my moves and records a winning end."""
        # Act.
        game = analyzed_conn.execute(
            "SELECT outcome, end_method, end_state FROM games "
            "WHERE game_uuid = 'golden-1'").fetchone()
        # Assert.
        assert (game["outcome"], game["end_method"]) == ("win", "checkmate")
        assert game["end_state"] == "winning"          # mate is unambiguous
        assert _my_mistakes(analyzed_conn, "golden-1") == []
