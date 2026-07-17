"""Termination classification and the resign winning/losing split."""

from collections import Counter

from chesstrain import patterns


def test_classify_termination():
    assert patterns.classify_termination("draw", "Game drawn") == ("draw", "draw")
    assert patterns.classify_termination(
        "win", "alice won by checkmate") == ("win", "checkmate")
    assert patterns.classify_termination(
        "loss", "bob won on time") == ("loss", "on time")
    assert patterns.classify_termination(
        "win", "bob won by resignation") == ("win", "resignation")


def test_eco_opening_names_picks_most_common(conn):
    rows = [("C20", "King's Pawn"), ("C20", "King's Pawn"),
            ("C20", "Bongcloud"), ("B01", "Scandinavian")]
    for i, (eco, opening) in enumerate(rows):
        conn.execute(
            "INSERT INTO games(game_uuid, username, is_me, outcome, eco, opening, "
            "end_time) VALUES(?,?,1,'win',?,?,?)",
            (f"g{i}", "alice", eco, opening, 1000 + i))
    conn.commit()
    names = patterns.eco_opening_names(conn)
    assert names["C20"] == "King's Pawn"  # most common wins the tie-break
    assert names["B01"] == "Scandinavian"


def test_resign_bucket_pov():
    b = patterns._resign_bucket
    # I resigned (loss); last ply was my opponent (is_me=0). Their eval -300 means
    # I stood +300 — I threw a win.
    assert b("loss", -300, 0) == "resign while winning"
    assert b("loss", 500, 0) == "resign while losing"
    # Opponent resigned (win); last ply was mine (is_me=1). My +400 => they were
    # lost => a normal win.
    assert b("win", 400, 1) == "resign while losing"
    assert b("win", -300, 1) == "resign while winning"
    # Not analyzed -> no eval.
    assert b("loss", None, None) == "resign (unclear)"


def test_termination_breakdown_splits_resignations(conn):
    def add(uuid, outcome, term, last_eval, last_is_me):
        conn.execute(
            "INSERT INTO games(game_uuid, username, is_me, outcome, termination, "
            "end_time, analyzed) VALUES(?,?,1,?,?,?,1)",
            (uuid, "alice", outcome, term, 1000))
        gid = conn.execute(
            "SELECT id FROM games WHERE game_uuid=?", (uuid,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO moves(game_id, ply, is_me, eval_cp_after) "
            "VALUES(?,?,?,?)", (gid, 30, last_is_me, last_eval))

    add("r1", "loss", "opp won by resignation", -300, 0)  # resigned won game
    add("r2", "loss", "opp won by resignation", 400, 0)   # resigned lost game
    add("r3", "win", "opp won by resignation", 250, 1)    # normal win
    conn.commit()

    # Sum across win/loss rows — "resign while losing" spans both (I resigned a
    # lost game; my opponent resigned a lost game).
    methods: Counter = Counter()
    for r in patterns.termination_breakdown(conn, {}):
        methods[r["method"]] += r["count"]
    assert methods["resign while winning"] == 1
    assert methods["resign while losing"] == 2
    assert "resignation" not in methods  # coarse bucket fully refined
