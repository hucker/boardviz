"""Smoke test: every page renders in a simulated Streamlit runtime without error.

Exercises the real DB (whatever is in data/), so it covers the with-data render
paths, not just empty state. Uses ``AppTest.from_string`` (not ``from_function``)
so each page runs with its module-level imports intact.
"""

import chess
import pytest
from streamlit.testing.v1 import AppTest

from chesstrain import cct, db
from chesstrain.ui import board as boardui
from chesstrain.ui import common

PAGES = ["import_page", "dashboard", "review_page", "mate_page", "trainer_page"]


class TestPageRendering:
    """Each page renders without raising — a baseline that its surface works."""

    @pytest.mark.spec("IMP-FETCH", "DASH-COUNT", "REV-CLUST", "MATE-GRID",
                      "TRN-DRILL")
    @pytest.mark.parametrize("module", PAGES)
    def test_page_renders_without_exception(self, module):
        """The page's render() runs to completion with no uncaught exception."""
        # Arrange: a one-line script that imports and renders the page.
        script = f"from chesstrain.ui import {module} as p\np.render()\n"
        # Act.
        app = AppTest.from_string(script).run(timeout=60)
        # Assert.
        assert not app.exception, f"{module} raised: {app.exception}"


class TestScanPayload:
    """The sets → frontend translation for the both-ways CCT board (TRN-CCT).

    The CCv2 element only renders in a live app, so the testable seam is the
    ``data`` payload board_scan hands it (see board.scan_payload).
    """

    @pytest.mark.spec("TRN-CCT")
    def test_payload_carries_both_sides_and_orientation(self):
        """A real position yields both sides' sets as sorted lists, oriented right."""
        # Arrange: Italian-ish position, White to move (Qf3 eyes f7).
        board = chess.Board(
            "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 1")
        # Act.
        data = boardui.scan_payload(board, cct.scan_both(board))
        # Assert: shape, orientation, and that Qxf7+ shows as a White check.
        assert data["orientation"] == "white" and data["turn"] == "w"
        assert set(data["me"]) == {"checks", "captures", "threats"}
        assert set(data["opp"]) == {"checks", "captures", "threats"}
        assert data["me"]["checks"] == sorted(data["me"]["checks"])  # JSON-ready
        assert "f3f7" in data["me"]["checks"]

    @pytest.mark.spec("TRN-CCT")
    def test_payload_orients_and_flips_turn_for_black(self):
        """When Black is to move the board flips and turn is 'b'."""
        # Arrange.
        board = chess.Board(
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
        # Act.
        data = boardui.scan_payload(board, cct.scan_both(board),
                                    reveal=True, played="d7d5")
        # Assert.
        assert data["orientation"] == "black" and data["turn"] == "b"
        assert data["reveal"] is True and data["played"] == "d7d5"



class TestProfilePickerHelp:
    """The Player selector's help shows the profile's data breakdown (FLT-DIMS)."""

    @pytest.mark.spec("FLT-DIMS")
    def test_help_counts_games_by_time_control(self):
        """The blurb reports total, analyzed, and per-time-control counts."""
        # Arrange: four games for 'alice' across time controls (two analyzed),
        # spanning 2020 -> 2023 in end_time.
        conn = db.connect(":memory:")
        db.init_db(conn)
        rows = [("blitz", 1, 1_600_000_000), ("blitz", 1, 1_650_000_000),
                ("bullet", 0, 1_680_000_000), ("rapid", 0, 1_700_000_000)]
        for i, (tc, an, et) in enumerate(rows):
            conn.execute(
                "INSERT INTO games(id, game_uuid, username, tc_class, analyzed, "
                "end_time) VALUES(?,?,?,?,?,?)", (i, f"g{i}", "alice", tc, an, et))
        conn.commit()
        # Act.
        text = common.profile_help_text(conn, "alice")
        svg = common._tc_bar_svg({"blitz": 2, "bullet": 1, "rapid": 1})
        # Assert: summary text, a date range, and an embedded SVG bar-chart image.
        assert "4 games" in text
        assert "2 analyzed" in text
        assert "2020" in text and "2023" in text  # date-range span
        assert "data:image/svg+xml;base64," in text
        # The chart labels every present time control with its count.
        for tc in ("blitz", "bullet", "rapid"):
            assert f">{tc}<" in svg
        assert svg.count("<rect") == 4  # background + one bar per time control
        conn.close()
