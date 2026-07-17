"""DB schema round-trips: game upsert/dedup, grade cache, filters, runs."""

import json

from chesstrain import db


def test_upsert_games_dedups_on_uuid(conn, records):
    assert db.upsert_games(conn, records, "alice", is_me=True) == 1
    # Re-inserting the same game inserts nothing (the cheap-reimport guarantee).
    assert db.upsert_games(conn, records, "alice", is_me=True) == 0
    rows = db.query_games(conn, username="alice")
    assert len(rows) == 1
    assert rows[0]["eco"] == "C20"
    assert rows[0]["tc_class"] == "blitz"
    assert rows[0]["analyzed"] == 0


def test_query_games_filters(conn, records):
    db.upsert_games(conn, records, "alice", is_me=True)
    assert len(db.query_games(conn, color="white")) == 1
    assert len(db.query_games(conn, color="black")) == 0
    assert len(db.query_games(conn, outcome="win")) == 1
    assert len(db.query_games(conn, tc_class="rapid")) == 0


def test_query_games_opening_substring(conn):
    for i, opening in enumerate(
            ["French Defense: Advance", "Sicilian Najdorf", "French Exchange"]):
        conn.execute(
            "INSERT INTO games(game_uuid, username, is_me, outcome, opening, "
            "end_time, analyzed) VALUES(?,?,1,'win',?,?,0)",
            (f"g{i}", "alice", opening, 1000 + i))
    conn.commit()
    assert len(db.query_games(conn, opening="French")) == 2  # substring
    assert len(db.query_games(conn, opening="french")) == 2  # case-insensitive
    assert len(db.query_games(conn, opening="Sicilian")) == 1
    assert len(db.query_games(conn, opening="Caro-Kann")) == 0


def test_grade_cache_round_trip(conn):
    grades = {"e2e4": 2, "d2d4": 1, "a2a3": -2}
    db.upsert_grade(conn, "EPDKEY", grades, "e2e4", 37, 12, ts=1.0)
    row = db.get_grade(conn, "EPDKEY")
    assert row is not None
    assert json.loads(row["grades_json"]) == grades
    assert row["best_uci"] == "e2e4"


def test_import_run_progress(conn):
    rid = db.start_run(conn, "alice", "analyze", total=3, ts=1.0)
    db.update_run(conn, rid, done=2, ts=2.0)
    db.update_run(conn, rid, status="done", ts=3.0)
    run = db.latest_run(conn, "alice", "analyze")
    assert run is not None
    assert run["done"] == 2 and run["status"] == "done"


def test_unanalyzed_and_mark(conn, records):
    db.upsert_games(conn, records, "alice", is_me=True)
    pending = db.unanalyzed_games(conn, "alice")
    assert len(pending) == 1
    db.mark_analyzed(conn, pending[0]["id"])
    assert len(db.unanalyzed_games(conn, "alice")) == 0
