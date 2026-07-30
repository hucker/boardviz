"""Trainer: position selection (modes, filters, dedup) and the replay (intro)."""

import chess
import pytest

from boardviz import cct, trainer
from boardviz.ui import trainer_page as tp


def _add_position(
    conn,
    *,
    game_id,
    epd,
    structure,
    move_type,
    phase,
    drop_cp,
    username="alice",
    ply=10,
    opening=None,
    fullmove=None,
    solve_depth=None,
):
    """Seed one drillable position: a game, my mistake there, and its grades."""
    conn.execute(
        "INSERT OR IGNORE INTO games(id, game_uuid, username, is_me, tc_class, "
        "opening) VALUES(?,?,?,1,'blitz',?)",
        (game_id, f"g{game_id}", username, opening),
    )
    conn.execute(
        "INSERT INTO mistakes(game_id, is_me, epd, fen, played_uci, structure, "
        "move_type, phase, drop_cp, ply, fullmove) VALUES(?,1,?,?,'e2e4',?,?,?,?,?,?)",
        (
            game_id,
            epd,
            f"fen-{epd}",
            structure,
            move_type,
            phase,
            drop_cp,
            ply,
            fullmove,
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO grades_cache(epd, grades_json, best_uci, eval_cp, "
        "depth, created_ts, solve_depth) VALUES(?,'{}','d2d4',0,12,1.0,?)",
        (epd, solve_depth),
    )


@pytest.fixture
def drill_conn(conn):
    """Three graded positions of varying pattern/severity for 'alice'.

    EPD1 open-centre quiet middlegame drop 100 (also blundered again in g4);
    EPD2 closed-centre capture opening drop 300 (later drilled and failed);
    EPD3 open-centre quiet endgame drop 200 (later drilled and passed).
    """
    _add_position(
        conn,
        game_id=1,
        epd="EPD1",
        structure="open center",
        move_type="quiet",
        phase="middlegame",
        drop_cp=100,
    )
    _add_position(
        conn,
        game_id=2,
        epd="EPD2",
        structure="closed center",
        move_type="capture",
        phase="opening",
        drop_cp=300,
    )
    _add_position(
        conn,
        game_id=3,
        epd="EPD3",
        structure="open center",
        move_type="quiet",
        phase="endgame",
        drop_cp=200,
    )
    # EPD1 blundered a second time (different game) — makes it "repeated".
    _add_position(
        conn,
        game_id=4,
        epd="EPD1",
        structure="open center",
        move_type="quiet",
        phase="middlegame",
        drop_cp=100,
    )
    # Prior drill attempts: EPD2 failed (grade 0), EPD3 passed (grade 2).
    for epd, grade in (("EPD2", 0), ("EPD3", 2)):
        conn.execute(
            "INSERT INTO attempts(epd, grade, created_ts) VALUES(?,?,2.0)", (epd, grade)
        )
    conn.commit()
    return conn


def _epds(positions):
    """The EPDs of a selection, in order."""
    return [p["epd"] for p in positions]


class TestSelectionModes:
    """Ordering modes: worst-first and repeat-my-failures (TRN-MODE)."""

    @pytest.mark.spec("TRN-MODE")
    def test_worst_mode_orders_by_biggest_eval_drop(self, drill_conn):
        """'worst' returns positions biggest-blunder first."""
        # Act.
        got = trainer.select_positions(drill_conn, mode="worst", username="alice")
        # Assert: 300, then 200, then 100.
        assert _epds(got) == ["EPD2", "EPD3", "EPD1"]

    @pytest.mark.spec("TRN-MODE")
    def test_repeat_failures_mode_keeps_only_positions_failed_before(self, drill_conn):
        """'repeat_failures' drills only positions attempted and failed (grade < 1)."""
        # Act.
        got = trainer.select_positions(
            drill_conn, mode="repeat_failures", username="alice"
        )
        # Assert: EPD2 failed; EPD3 passed and is excluded.
        assert _epds(got) == ["EPD2"]

    @pytest.mark.spec("TRN-MODE")
    def test_default_mode_returns_the_whole_pool(self, drill_conn):
        """The default (random) mode samples the full deduped pool."""
        # Act.
        got = trainer.select_positions(drill_conn, username="alice")
        # Assert: one puzzle per position, all three present (order unspecified).
        assert set(_epds(got)) == {"EPD1", "EPD2", "EPD3"}


class TestPatternFilters:
    """Structure / move-type / phase filters, and that they compose (TRN-PATRN)."""

    @pytest.mark.spec("TRN-PATRN")
    def test_each_pattern_dimension_narrows_the_pool(self, drill_conn):
        """A single pattern filter keeps only matching positions."""
        # Act + Assert.
        assert set(
            _epds(
                trainer.select_positions(
                    drill_conn, username="alice", structure="open center"
                )
            )
        ) == {"EPD1", "EPD3"}
        assert _epds(
            trainer.select_positions(drill_conn, username="alice", move_type="capture")
        ) == ["EPD2"]
        assert _epds(
            trainer.select_positions(drill_conn, username="alice", phase="opening")
        ) == ["EPD2"]

    @pytest.mark.spec("TRN-PATRN")
    def test_pattern_filters_compose(self, drill_conn):
        """Independent pattern filters AND together."""
        # Act: open-centre AND endgame is only EPD3 (EPD1 is a middlegame).
        got = trainer.select_positions(
            drill_conn, username="alice", structure="open center", phase="endgame"
        )
        # Assert.
        assert _epds(got) == ["EPD3"]

    @pytest.mark.spec("TRN-PATRN")
    def test_opening_filter_scopes_the_drill_to_one_line(self, conn):
        """Exact and word-contains opening filters both scope the drill."""
        # Arrange: two French Advance variants and an unrelated opening.
        _add_position(
            conn,
            game_id=1,
            epd="A1",
            structure="s",
            move_type="quiet",
            phase="opening",
            drop_cp=200,
            opening="French Defense Advance Nimzowitsch System",
        )
        _add_position(
            conn,
            game_id=2,
            epd="A2",
            structure="s",
            move_type="quiet",
            phase="opening",
            drop_cp=200,
            opening="French Defense Advance Paulsen Attack",
        )
        _add_position(
            conn,
            game_id=3,
            epd="IT",
            structure="s",
            move_type="quiet",
            phase="opening",
            drop_cp=200,
            opening="Italian Game",
        )
        conn.commit()
        # Exact name matches just that one variant.
        assert _epds(
            trainer.select_positions(
                conn, username="alice", opening="French Defense Advance Paulsen Attack"
            )
        ) == ["A2"]
        # Word-contains catches every French Advance variant in one query...
        assert set(
            _epds(
                trainer.select_positions(
                    conn, username="alice", opening_like="french advance"
                )
            )
        ) == {"A1", "A2"}
        # ...and the words may be non-adjacent in the name; 'french' alone is broader.
        assert (
            len(trainer.select_positions(conn, username="alice", opening_like="french"))
            == 2
        )

    @pytest.mark.spec("TRN-PATRN")
    def test_max_fullmove_caps_the_drill_to_the_early_opening(self, conn):
        """max_fullmove keeps only positions at or before that move number."""
        # Arrange: an early opening position and a deep one.
        _add_position(
            conn,
            game_id=1,
            epd="EARLY",
            structure="s",
            move_type="quiet",
            phase="opening",
            drop_cp=200,
            fullmove=5,
        )
        _add_position(
            conn,
            game_id=2,
            epd="DEEP",
            structure="s",
            move_type="quiet",
            phase="opening",
            drop_cp=200,
            fullmove=14,
        )
        conn.commit()
        # Act + Assert: capping at move 6 drops the move-14 position.
        assert _epds(
            trainer.select_positions(conn, username="alice", max_fullmove=6)
        ) == ["EARLY"]


class TestRepeatedAndLength:
    """The 'repeated only' toggle and the drill-length cap (TRN-REPEAT, TRN-LEN)."""

    @pytest.mark.spec("TRN-REPEAT")
    def test_repeated_only_keeps_positions_blundered_more_than_once(self, drill_conn):
        """'repeated_only' keeps only positions blundered 2+ times across games."""
        # Act: only EPD1 was blundered in two games.
        got = trainer.select_positions(drill_conn, username="alice", repeated_only=True)
        # Assert.
        assert _epds(got) == ["EPD1"]

    @pytest.mark.spec("TRN-DIFF")
    def test_difficulty_filter_keeps_only_the_harder_finds(self, conn):
        """min_solve_depth drops positions whose best move surfaces too shallow."""
        # Arrange: an obvious find (depth 3) and a hard one (depth 9).
        _add_position(
            conn,
            game_id=1,
            epd="EASY",
            structure="s",
            move_type="quiet",
            phase="middlegame",
            drop_cp=200,
            solve_depth=3,
        )
        _add_position(
            conn,
            game_id=2,
            epd="HARD",
            structure="s",
            move_type="quiet",
            phase="middlegame",
            drop_cp=200,
            solve_depth=9,
        )
        conn.commit()
        # Act + Assert: requiring depth >= 6 keeps only the hard one.
        assert _epds(
            trainer.select_positions(conn, username="alice", min_solve_depth=6)
        ) == ["HARD"]

    @pytest.mark.spec("TRN-LEN")
    def test_drill_length_caps_the_number_of_positions(self, drill_conn):
        """`n` caps the drill length (using 'worst' for a deterministic pick)."""
        # Act.
        got = trainer.select_positions(drill_conn, n=2, mode="worst", username="alice")
        # Assert: the two biggest blunders only.
        assert _epds(got) == ["EPD2", "EPD3"]


class TestPositionSelection:
    """Choosing drill positions from the player's graded mistakes."""

    @pytest.mark.spec("TRN-UNIQ")
    def test_a_position_blundered_twice_yields_one_puzzle(self, conn):
        """The same position across two games drills once, not once per game."""
        # Arrange: the same position (EPD1) blundered in two games.
        for gid in (1, 2):
            conn.execute(
                "INSERT INTO games(id, game_uuid, username, is_me, tc_class) "
                "VALUES(?,?,?,1,'blitz')",
                (gid, f"g{gid}", "alice"),
            )
            conn.execute(
                "INSERT INTO mistakes(game_id, is_me, epd, fen, played_uci, "
                "structure, move_type, phase, drop_cp, ply) "
                "VALUES(?,1,'EPD1','fen1','e2e4','open center','quiet',"
                "'middlegame',300,10)",
                (gid,),
            )
        conn.execute(
            "INSERT INTO grades_cache(epd, grades_json, best_uci, eval_cp, depth, "
            "created_ts) VALUES('EPD1','{}','d2d4',0,12,1.0)"
        )
        conn.commit()
        # Act.
        positions = trainer.select_positions(conn, n=40, username="alice")
        # Assert.
        assert len(positions) == 1
        assert positions[0]["epd"] == "EPD1"


class TestCommitMove:
    """Scoring/recording an answered move, shared by the puzzle and CCT beats."""

    @pytest.mark.spec("TRN-SCORE")
    def test_commit_scores_and_records_the_attempt(self, conn, monkeypatch):
        """A best move scores +1, updates the running total, and writes an attempt."""
        # Arrange: st.rerun would abort outside a live script — stub it out.
        monkeypatch.setattr(tp.st, "rerun", lambda: None)
        board = chess.Board()
        state = {"total": 0.0, "answered": 0}
        pos = {"grades": {"e2e4": 2}, "epd": "EPD1", "tc_class": "blitz"}
        # Act.
        tp._commit_move(conn, pos, board, state, {"uci": "e2e4", "ms": 1500})
        # Assert: scored, tallied, and persisted.
        assert state["result"]["final_score"] == 1.0
        assert state["result"]["san"] == "e4"
        assert (state["total"], state["answered"]) == (1.0, 1)
        row = conn.execute(
            "SELECT grade, final_score FROM attempts WHERE epd='EPD1'"
        ).fetchone()
        assert tuple(row) == (2, 1.0)

    @pytest.mark.spec("TRN-CCT")
    def test_commit_carries_the_cct_marks(self, conn, monkeypatch):
        """A CCT beat's per-layer marks ride along on the result."""
        # Arrange.
        monkeypatch.setattr(tp.st, "rerun", lambda: None)
        marked = {"checks": ["e2e4"], "captures": [], "threats": ["d5"]}
        pos = {"grades": {"e2e4": 2}, "epd": "EPD2", "tc_class": "blitz"}
        state = {}
        # Act.
        tp._commit_move(
            conn,
            pos,
            chess.Board(),
            state,
            {"uci": "e2e4", "ms": 100, "marked": marked},
        )
        # Assert.
        assert state["result"]["marked"] == marked

    @pytest.mark.spec("TRN-RETRY")
    def test_practice_pass_records_no_attempt(self, conn, monkeypatch):
        """An unscored practice pass scores the move but writes no attempt row."""
        # Arrange: a practice-flagged drill state.
        monkeypatch.setattr(tp.st, "rerun", lambda: None)
        board = chess.Board()
        state = {"total": 0.0, "answered": 0, "practice": True}
        pos = {"grades": {"e2e4": 2}, "epd": "PRAC1", "tc_class": "blitz"}
        # Act.
        tp._commit_move(conn, pos, board, state, {"uci": "e2e4", "ms": 1500})
        # Assert: still scored for feedback, but nothing persisted.
        assert state["result"]["final_score"] == 1.0
        row = conn.execute(
            "SELECT 1 FROM attempts WHERE epd='PRAC1'").fetchone()
        assert row is None


class TestRetryMisses:
    """Selecting a drill's misses for the unscored retry (TRN-RETRY)."""

    @pytest.mark.spec("TRN-RETRY")
    def test_missed_positions_picks_bad_moves_and_incomplete_scans(self):
        """A miss is a sub-best move or (on CCT) any category not fully found."""
        # Arrange: p0 clean+best, p1 bad move, p2 best move but missed a scan,
        # p3 not yet answered (beyond outcomes) — only p1 and p2 are misses.
        state = {
            "queue": [{"epd": "p0"}, {"epd": "p1"}, {"epd": "p2"}, {"epd": "p3"}],
            "outcomes": [1.0, 0.0, 1.0],
            "cct_rows": [
                {"checks": 1.0, "captures": 1.0, "threats": 1.0},
                {"checks": 1.0, "captures": 1.0, "threats": 1.0},
                {"checks": 0.5, "captures": 1.0, "threats": 1.0},
            ],
        }
        # Act.
        missed = tp._missed_positions(state)
        # Assert.
        assert [p["epd"] for p in missed] == ["p1", "p2"]

    @pytest.mark.spec("TRN-RETRY")
    def test_start_practice_seeds_an_unscored_queue(self, monkeypatch):
        """Starting a retry loads the misses and flags the pass as practice."""
        # Arrange: a minimal session_state that supports item + attribute access
        # (Streamlit maps both to one store).
        class _SS(dict):
            def __getattr__(self, k):
                try:
                    return self[k]
                except KeyError as exc:
                    raise AttributeError(k) from exc

            def __setattr__(self, k, v):
                self[k] = v
        monkeypatch.setattr(tp.st, "session_state", _SS())
        missed = [{"epd": "p1"}, {"epd": "p2"}]
        # Act.
        tp._start_practice({"cct_scope": "opp"}, missed)
        # Assert.
        new = tp.st.session_state.trainer
        assert new["practice"] is True
        assert new["queue"] == missed
        assert new["cct_scope"] == "opp"
        assert new["i"] == 0 and new["outcomes"] == []


class TestCctCounts:
    """Scoring the per-layer marks against the true both-ways sets (TRN-CCT)."""

    @pytest.mark.spec("TRN-CCT")
    def test_counts_correct_marks_and_ignores_wrong_ones(self):
        """A correct check and threat count; a wrong check mark does not."""
        # Arrange: White Ra1 checks via a1a8 (open 8th); White Bg1 wins Nd4 (loose).
        board = chess.Board("4k3/8/8/8/3n4/8/8/R3K1B1 w - - 0 1")
        scan = cct.scan_both(board)
        # a1a8 is a real check; d4 a real threat; a1a5 is not a check (wrong mark).
        marked = {"checks": ["a1a8", "a1a5"], "captures": [], "threats": ["d4"]}
        # Act.
        found = tp._cct_counts(board, marked, scan)
        # Assert.
        assert found["me"]["checks"] == 1
        assert found["me"]["threats"] == 1
        assert found["me"]["captures"] == 0


class TestAccumulateCct:
    """The drill's running CCT tally and the position complete flag (TRN-CCT)."""

    # White: Ra8+ (a1a8), Bxd4 (g1d4, a capture) and Nd4 is a threat. Black (opp)
    # to answer has two knight checks: Nc2+ (d4c2) and Nf3+ (d4f3), and no
    # captures or loose pieces — so its capture/loose buckets are empty.
    _BOARD = "4k3/8/8/8/3n4/8/8/R3K1B1 w - - 0 1"
    _ALL = {"checks": ["a1a8", "d4c2", "d4f3"], "captures": ["g1d4"], "threats": ["d4"]}

    @pytest.mark.spec("TRN-CCT")
    def test_all_found_is_complete_empty_buckets_are_free(self):
        """Marking every real item is a clean scan — empty buckets need no action."""
        # Arrange: every real item marked; the empty opp buckets are trivially done.
        board = chess.Board(self._BOARD)
        state: dict = {}
        # Act.
        complete, _f, _a, _cells = tp._accumulate_cct(
            state, board, dict(self._ALL))
        # Assert: complete, and the running tally reflects the finds.
        assert complete is True
        assert state["cct_found"]["me"]["checks"] == 1
        assert state["cct_found"]["opp"]["checks"] == 2
        assert state["cct_avail"]["me"]["threats"] == 1

    @pytest.mark.spec("TRN-CCT")
    def test_missing_a_real_item_is_incomplete(self):
        """Leaving a real check unmarked breaks the clean scan."""
        # Arrange: everything but one of Black's knight checks.
        board = chess.Board(self._BOARD)
        state: dict = {}
        marked = {**self._ALL, "checks": ["a1a8", "d4c2"]}  # missing d4f3
        # Act.
        complete, *_ = tp._accumulate_cct(state, board, marked)
        # Assert.
        assert complete is False

    @pytest.mark.spec("TRN-CCT")
    def test_tallies_accumulate_across_positions(self):
        """Two positions add up in the running found/available totals."""
        # Arrange: the same position twice, marking only the check each time.
        board = chess.Board(self._BOARD)
        state: dict = {}
        marked = {"checks": ["a1a8"], "captures": [], "threats": []}
        # Act.
        tp._accumulate_cct(state, board, marked)
        tp._accumulate_cct(state, board, marked)
        # Assert: found checks 1+1, available checks 1+1.
        assert state["cct_found"]["me"]["checks"] == 2
        assert state["cct_avail"]["me"]["checks"] == 2

    @pytest.mark.spec("TRN-CCT")
    def test_position_score_averages_sides_over_four(self):
        """Score = each category's mean per-side fraction + the move, out of 4;
        an empty bucket is trivially found (worth its full share)."""
        # checks: me 1/1, opp 2/2 -> 1.0; captures: me 1/1, opp 0/1 -> 0.5;
        # loose: me 1/1, opp empty (1.0) -> (1.0+1.0)/2 = 1.0. Scan = 2.5.
        found = {"me": {"checks": 1, "captures": 1, "threats": 1},
                 "opp": {"checks": 2, "captures": 0, "threats": 0}}
        avail = {"me": {"checks": 1, "captures": 1, "threats": 1},
                 "opp": {"checks": 2, "captures": 1, "threats": 0}}
        cells = tp._cct_cells(found, avail, "both")
        assert tp._cct_position_score(cells, 1.0) == 3.5   # 2.5 scan + best move

    @pytest.mark.spec("TRN-CCT")
    def test_played_forcing_move_is_credited_even_if_unmarked(self):
        """Playing a check/capture you forgot to mark still counts as found."""
        # Arrange: White to move — Qf3xf7 is both a capture and a check.
        board = chess.Board(
            "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 1")
        marked = {"checks": [], "captures": [], "threats": []}
        # Act: you played Qxf7+ but marked nothing.
        out = tp._credit_played_move(board, "f3f7", marked)
        # Assert: the played move is credited as both a capture and a check…
        assert "f3f7" in out["captures"]
        assert "f3f7" in out["checks"]
        # …while a quiet move invents nothing, and the input isn't mutated.
        quiet = tp._credit_played_move(board, "b1c3", marked)
        assert quiet["captures"] == [] and quiet["checks"] == []
        assert marked == {"checks": [], "captures": [], "threats": []}

    @pytest.mark.spec("TRN-CCT")
    def test_missed_items_are_listed_in_algebra(self):
        """Unmarked scan items come back as SAN (moves) / piece+square (threats)."""
        # Arrange: Qf3xf7 is a missed check-and-capture for White.
        board = chess.Board(
            "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 1")
        scan = cct.scan_both(board)
        # Act: nothing marked → everything is missed.
        missed = tp._cct_missed(
            board, {"checks": [], "captures": [], "threats": []}, scan)
        # Assert: the capturing move shows as proper SAN (a mate here) in both
        # its layers.
        assert "Qxf7#" in missed["me"]["checks"]
        assert "Qxf7#" in missed["me"]["captures"]
        # Threat labels are piece-letter + square (pawns bare).
        assert tp._threat_label(board, "c6") == "Nc6"  # black knight
        assert tp._threat_label(board, "e5") == "e5"   # black pawn

    @pytest.mark.spec("TRN-CCT")
    def test_opponent_missed_moves_disambiguate_san(self):
        """Opponent SAN is taken on their turn, so two rook captures of one square
        disambiguate (Raxb2/Rcxb2) instead of both reading the ambiguous Rxb2."""
        # Black to move; White's rooks on a2 and c2 both capture the b2 pawn.
        board = chess.Board("4k3/8/8/8/8/8/RpR5/4K3 b - - 0 1")
        missed = tp._cct_missed(
            board, {"checks": [], "captures": [], "threats": []},
            cct.scan_both(board))
        assert "Raxb2" in missed["opp"]["captures"]
        assert "Rcxb2" in missed["opp"]["captures"]
        assert "Rxb2" not in missed["opp"]["captures"]  # the wrong-turn artifact

    @pytest.mark.spec("TRN-CCT")
    def test_scoreboard_svg_renders_totals_and_bars(self):
        """The compact scoreboard graphic shows per-category and total counts."""
        # Arrange: found 5 of 13 across the six categories.
        found = {"me": {"checks": 2, "captures": 1, "threats": 0},
                 "opp": {"checks": 1, "captures": 0, "threats": 1}}
        avail = {"me": {"checks": 3, "captures": 4, "threats": 2},
                 "opp": {"checks": 1, "captures": 1, "threats": 2}}
        # Act.
        svg = tp._cct_scoreboard_svg(found, avail)
        # Assert.
        assert svg.startswith("<svg") and "CCT scan tally" in svg
        assert "2/3" in svg  # me checks found/available
        assert "5/13" in svg  # grand total
        assert svg.count("<rect") >= 8  # white card + the six bars + total track
        # A green m/n score note (baked in, coloured for a perfect run).
        green = tp._cct_scoreboard_svg(found, avail, note="5/5", note_color="#16a34a")
        assert 'fill="#16a34a"' in green and ">5/5<" in green


class TestProgressGrid:
    """The running-progress grid: a Move row always, category rows on CCT (TRN-TALLY)."""

    @pytest.mark.spec("TRN-TALLY")
    def test_move_only_grid_when_not_a_cct_drill(self):
        """With no CCT rows the grid has just the Move row, and the score count."""
        # Arrange: two answered of five (a best move, then a blunder).
        svg = tp._progress_grid_svg([1.0, 0.0], [], 5)
        # Assert.
        assert svg.startswith("<svg") and ">Move<" in svg
        assert ">Checks<" not in svg  # no category rows off a plain drill
        assert ">1/5<" in svg  # one correct of five

    @pytest.mark.spec("TRN-TALLY", "TRN-CCT")
    def test_cct_grid_adds_a_row_per_category(self):
        """A CCT drill's grid gains Checks / Captures / Loose rows."""
        # Arrange: one answered CCT position (all checks, half captures, no loose).
        rows = [{"checks": 1.0, "captures": 0.5, "threats": 0.0}]
        svg = tp._progress_grid_svg([1.0], rows, 3)
        # Assert: the three category rows are labelled.
        for label in (">Move<", ">Checks<", ">Captures<", ">Loose<"):
            assert label in svg

    @pytest.mark.spec("TRN-TALLY")
    def test_category_cells_align_with_their_move_column(self):
        """cct_rows is index-aligned: a CCT position after non-CCT ones keeps its
        category cell in the same column as its Move cell (a None pads the gap)."""
        # Arrange: two non-CCT answers, then a fully-found CCT position.
        outcomes = [1.0, 0.0, 1.0]
        cct_rows = [None, None, {"checks": 1.0, "captures": 1.0, "threats": 1.0}]
        svg = tp._progress_grid_svg(outcomes, cct_rows, 5)
        # Assert: the Checks row has exactly one coloured (green) cell — the third
        # column — with the first two grey, i.e. no left-shift.
        assert svg.count(f'fill="{tp._OK_GREEN}"') >= 4  # count note + move + 3 cats
        # A category row with only a None-padded prefix must not paint column 0.
        rows = [None, {"checks": 1.0, "captures": 0.0, "threats": 0.0}]
        one = tp._progress_grid_svg([0.0, 1.0], rows, 2)
        # Checks green belongs to column 1 (x≈), never column 0 (x=58 region).
        assert ">Checks<" in one

    @pytest.mark.spec("TRN-TALLY")
    def test_frac_colour_thresholds(self):
        """A category cell is green when complete, amber when partial, red when none."""
        assert tp._frac_color(1.0) == tp._OK_GREEN
        assert tp._frac_color(0.4) == tp._MID_AMBER
        assert tp._frac_color(0.0) == tp._BAD_RED


class TestSideIndicator:
    """The prominent 'which colour am I playing' banner (TRN-INTRO)."""

    @pytest.mark.spec("TRN-INTRO")
    def test_side_line_names_white_when_white_is_to_move(self):
        """The opening position (White to move) is labelled White."""
        # Act.
        line = tp._side_line(chess.Board())
        # Assert.
        assert "White" in line and "Black" not in line

    @pytest.mark.spec("TRN-INTRO")
    def test_side_line_names_black_when_black_is_to_move(self):
        """A black-to-move position is labelled Black."""
        # Arrange: same start position but Black to move.
        board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1")
        # Act.
        line = tp._side_line(board)
        # Assert.
        assert "Black" in line and "White" not in line


class TestIntro:
    """The pre-move context: opponent's last move highlighted, no countdown."""

    @pytest.mark.spec("TRN-INTRO")
    def test_intro_highlights_the_opponent_last_move(self):
        """The intro carries the opponent's last move and no delay (self-paced)."""
        # Act.
        b = tp._intro_for({"opp_move": "e2e4"})
        # Assert.
        assert b["delayMs"] == 0
        assert b["lastMove"] == "e2e4"

    @pytest.mark.spec("TRN-INTRO")
    def test_intro_when_there_is_no_prior_move(self):
        """A first-move position has nothing to highlight."""
        # Act.
        b = tp._intro_for({})
        # Assert.
        assert b["lastMove"] is None


class TestMateDrill:
    """The forced-mate drill: selection filters and scoring (TRN-MATE)."""

    _FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    _MATE = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"  # White: Ra8 is checkmate.

    def _seed(self, conn):
        conn.execute("INSERT INTO games(id, game_uuid, username, is_me, tc_class) "
                     "VALUES(1,'g1','alice',1,'blitz')")
        for i, dist, key, conv in ((1, 1, "e2e4", 1), (2, 1, "d2d4", 0),
                                    (3, 3, "g1f3", 0)):
            conn.execute(
                "INSERT INTO mate_chances(id, game_id, is_me, fen, distance, "
                "key_uci, mate_pv_json, motif, converted) "
                "VALUES(?,1,1,?,?,?,'[]','back-rank',?)",
                (i, self._FEN, dist, key, conv))
        conn.commit()

    @pytest.mark.spec("TRN-MATE")
    def test_m1_selects_only_mate_in_one(self, conn):
        """The M1 drill draws only distance-1 chances."""
        # Arrange.
        self._seed(conn)
        # Act.
        got = trainer.select_mate_positions(conn, username="alice")
        # Assert.
        assert {p["distance"] for p in got} == {1}
        assert len(got) == 2

    @pytest.mark.spec("TRN-MATE")
    def test_missed_only_keeps_the_blown_mate(self, conn):
        """missed_only drops converted chances, keeping the ones you failed."""
        # Arrange.
        self._seed(conn)
        # Act.
        got = trainer.select_mate_positions(conn, username="alice", missed_only=True)
        # Assert.
        assert [p["key_uci"] for p in got] == ["d2d4"]

    @pytest.mark.spec("TRN-MATE")
    def test_deep_selects_mate_in_two_plus(self, conn):
        """deep=True draws distance>=2 chances."""
        # Arrange.
        self._seed(conn)
        # Act.
        got = trainer.select_mate_positions(conn, username="alice", deep=True)
        # Assert.
        assert len(got) == 1 and got[0]["distance"] == 3

    @pytest.mark.spec("TRN-MATE")
    def test_m1_scored_by_delivering_mate(self):
        """M1 is correct for any move that checkmates, not just the stored one."""
        # Arrange.
        board = chess.Board(self._MATE)
        pos = {"distance": 1, "key_uci": "a1a8"}
        # Act + Assert.
        assert tp._mate_correct(board, "a1a8", pos) is True   # delivers mate
        assert tp._mate_correct(board, "a1a4", pos) is False  # legal, not mate

    @pytest.mark.spec("TRN-MATE")
    def test_deep_scored_by_the_key_move(self):
        """A deeper mate is correct only for the stored forcing key move."""
        # Arrange.
        board = chess.Board(self._MATE)
        pos = {"distance": 3, "key_uci": "a1a8"}
        # Act + Assert.
        assert tp._mate_correct(board, "a1a8", pos) is True
        assert tp._mate_correct(board, "a1a7", pos) is False
