"""Shared fixtures: a tiny chess.com-style game and a temp DB."""

import json

import pytest

from chesstrain import db
from chesstrain.blitz_analysis import load_games

# A 4-move Scholar's mate by "alice" (White), with %clk annotations.
PGN = (
    '[Event "Live Chess"]\n[Site "Chess.com"]\n[White "alice"]\n'
    '[Black "bob"]\n[Result "1-0"]\n[ECO "C20"]\n[TimeControl "180"]\n'
    '[Termination "alice won by checkmate"]\n\n'
    "1. e4 {[%clk 0:03:00]} e5 {[%clk 0:02:58]} "
    "2. Qh5 {[%clk 0:02:55]} Nc6 {[%clk 0:02:50]} "
    "3. Bc4 {[%clk 0:02:52]} Nf6 {[%clk 0:02:40]} "
    "4. Qxf7# {[%clk 0:02:50]} 1-0\n"
)


def game_dict(uuid="g-1", end_time=1_700_000_000):
    return {
        "url": f"https://www.chess.com/game/live/{uuid}",
        "pgn": PGN, "time_control": "180", "end_time": end_time, "uuid": uuid,
    }


@pytest.fixture
def records(tmp_path):
    path = tmp_path / "games.json"
    path.write_text(json.dumps({"games": [game_dict()]}))
    return load_games(path, username="alice", time_control=None)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()
