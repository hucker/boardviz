"""SQLite persistence — the only module that writes the database.

Design choices that matter (see the plan):

* **WAL mode** so the reader UI never blocks on the writer subprocess.
* **Position key = EPD** (placement+turn+castling+ep) for ``grades_cache`` and
  ``moves.epd_before`` so half/fullmove counters don't fragment the cache.
* **Eval sign = mover POV** everywhere (positive = good for side to move).
* ``games.game_uuid`` is UNIQUE and inserts use ``OR IGNORE`` so a repeat import
  never overwrites the ``analyzed`` flag — this is what makes re-imports cheap.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from . import config
from .blitz_analysis import GameRecord, Mistake

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    username    TEXT PRIMARY KEY,
    is_me       INTEGER NOT NULL DEFAULT 0,
    last_import_ts REAL
);

CREATE TABLE IF NOT EXISTS games (
    id          INTEGER PRIMARY KEY,
    game_uuid   TEXT UNIQUE,
    url         TEXT,
    username    TEXT NOT NULL,
    is_me       INTEGER NOT NULL DEFAULT 0,
    my_color    TEXT,               -- 'white' | 'black'
    outcome     TEXT,               -- 'win' | 'loss' | 'draw'
    termination TEXT,
    time_control TEXT,
    tc_class    TEXT,               -- 'bullet' | 'blitz' | 'rapid' | 'daily'
    end_time    REAL,
    flagged     INTEGER DEFAULT 0,
    eco         TEXT,
    opening     TEXT,
    pgn         TEXT,
    analyzed    INTEGER NOT NULL DEFAULT 0,
    -- End-of-game snapshot, filled by analysis (NULL until analysed). Precomputed
    -- so it's filterable and export-ready without re-deriving. See store_end_state.
    end_state   TEXT,               -- 'winning' | 'even' | 'losing' (my POV)
    end_eval_cp INTEGER,            -- my-POV eval (cp) at the final position
    end_clock_me  REAL,             -- my remaining clock (s) at game end
    end_clock_opp REAL,             -- opponent's remaining clock (s)
    end_pieces  INTEGER             -- non-king pieces on the board at the end
);

CREATE TABLE IF NOT EXISTS moves (
    id          INTEGER PRIMARY KEY,
    game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply         INTEGER NOT NULL,
    fullmove    INTEGER,
    color       TEXT,
    is_me       INTEGER,
    uci         TEXT,
    san         TEXT,
    epd_before  TEXT,
    eval_cp_before INTEGER,
    eval_cp_after  INTEGER,
    drop_cp     INTEGER,
    best_uci    TEXT,
    phase       TEXT,
    structure   TEXT,
    move_type   TEXT,
    seconds_spent     REAL,
    seconds_remaining REAL,
    is_long_think INTEGER DEFAULT 0,
    game_state  TEXT,               -- 'winning' | 'equal' | 'losing' (mover POV)
    UNIQUE(game_id, ply)
);

CREATE TABLE IF NOT EXISTS mistakes (
    id          INTEGER PRIMARY KEY,
    game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply         INTEGER,
    fen         TEXT,
    epd         TEXT,
    played_uci  TEXT,
    best_pv_json TEXT,
    fullmove    INTEGER,
    drop_cp     INTEGER,
    is_me       INTEGER,
    structure   TEXT,
    move_type   TEXT,
    phase       TEXT,
    eco         TEXT,
    game_state  TEXT,
    url         TEXT
);

CREATE TABLE IF NOT EXISTS grades_cache (
    epd         TEXT PRIMARY KEY,
    grades_json TEXT,
    best_uci    TEXT,
    eval_cp     INTEGER,
    depth       INTEGER,
    created_ts  REAL
);

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY,
    epd         TEXT,
    source      TEXT,
    played_uci  TEXT,
    grade       INTEGER,
    time_taken_s REAL,
    time_penalty INTEGER,
    final_score INTEGER,
    tc_class    TEXT,
    created_ts  REAL,
    correct     INTEGER
);

CREATE TABLE IF NOT EXISTS import_runs (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    kind        TEXT,               -- 'fetch' | 'analyze'
    status      TEXT,               -- 'running' | 'done' | 'error'
    total       INTEGER DEFAULT 0,
    done        INTEGER DEFAULT 0,
    started_ts  REAL,
    updated_ts  REAL,
    message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_games_user ON games(username, is_me);
CREATE INDEX IF NOT EXISTS idx_games_analyzed ON games(analyzed);
CREATE INDEX IF NOT EXISTS idx_moves_game ON moves(game_id);
CREATE INDEX IF NOT EXISTS idx_moves_state ON moves(is_me, is_long_think, game_state);
CREATE INDEX IF NOT EXISTS idx_mistakes_struct ON mistakes(structure);
CREATE INDEX IF NOT EXISTS idx_mistakes_type ON mistakes(move_type, phase);
CREATE INDEX IF NOT EXISTS idx_mistakes_eco ON mistakes(eco);
CREATE INDEX IF NOT EXISTS idx_attempts_epd ON attempts(epd);
"""


def connect(path: Path | str = config.DB_PATH) -> sqlite3.Connection:
    """Open a connection with WAL, foreign keys, and a busy timeout set."""
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


# Columns added to `games` after the initial release; ALTERed onto existing DBs.
_GAMES_ADDED_COLS = {
    "end_state": "TEXT", "end_eval_cp": "INTEGER", "end_clock_me": "REAL",
    "end_clock_opp": "REAL", "end_pieces": "INTEGER",
}


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes, and migrate older DBs (idempotent)."""
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(games)")}
    for col, typ in _GAMES_ADDED_COLS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE games ADD COLUMN {col} {typ}")
    conn.commit()


# --- games / players -------------------------------------------------------
def _opening_from_headers(game) -> tuple[str, str]:
    """Extract (eco, opening_name) from PGN headers, best-effort."""
    h = game.headers
    eco = h.get("ECO", "")
    opening = h.get("Opening", "")
    if not opening:
        url = h.get("ECOUrl", "")
        if url:
            opening = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    return eco, opening


def upsert_player(conn: sqlite3.Connection, username: str, is_me: bool,
                  ts: float | None = None) -> None:
    conn.execute(
        "INSERT INTO players(username, is_me, last_import_ts) VALUES(?,?,?) "
        "ON CONFLICT(username) DO UPDATE SET is_me=excluded.is_me, "
        "last_import_ts=COALESCE(excluded.last_import_ts, players.last_import_ts)",
        (username, int(is_me), ts),
    )
    conn.commit()


def upsert_games(conn: sqlite3.Connection, records: Iterable[GameRecord],
                 username: str, is_me: bool) -> int:
    """Insert games, ignoring any whose game_uuid already exists.

    Returns the number of newly inserted rows (analyzed=0). Existing rows keep
    their analyzed flag untouched — the basis of cheap re-imports.
    """
    import chess

    new = 0
    for rec in records:
        eco, opening = _opening_from_headers(rec.game)
        cur = conn.execute(
            "INSERT OR IGNORE INTO games("
            "game_uuid, url, username, is_me, my_color, outcome, termination, "
            "time_control, tc_class, end_time, flagged, eco, opening, pgn, analyzed"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (
                rec.uuid or rec.url, rec.url, username, int(is_me),
                chess.COLOR_NAMES[rec.my_color], rec.outcome, rec.termination,
                rec.time_control, config.tc_class(rec.time_control),
                rec.end_time.timestamp(), int(rec.flagged), eco, opening, rec.pgn,
            ),
        )
        new += cur.rowcount
    conn.commit()
    return new


def where_in(col: str, val) -> tuple[str, list]:
    """SQL fragment for a scalar / list / None filter value.

    ``None`` or an empty list -> ("", []) (no filter); a list -> "col IN (?,..)";
    a scalar -> "col = ?". Lets one filter accept a single value or a
    multi-select without the caller special-casing either shape.
    """
    if val is None:
        return "", []
    if isinstance(val, (list, tuple, set)):
        vals = list(val)
        if not vals:
            return "", []
        return f"{col} IN ({','.join('?' * len(vals))})", vals
    return f"{col} = ?", [val]


def query_games(conn: sqlite3.Connection, *, username: str | None = None,
                is_me: int | None = None,
                tc_class: str | list[str] | None = None,
                color: str | list[str] | None = None,
                outcome: str | list[str] | None = None,
                analyzed: int | None = None, flagged: int | None = None,
                eco: str | list[str] | None = None, opening: str | None = None,
                end_state: str | list[str] | None = None,
                min_end_time: float | None = None) -> list[sqlite3.Row]:
    """Filtered game listing (feeds both dashboard and trainer).

    Most filters accept a scalar or a list (matched with IN); ``opening`` is a
    case-insensitive substring match; ``min_end_time`` keeps only games at or
    after a timestamp (the 'most recent N games' scope).
    """
    where, params = [], []
    for col, val in (("username", username), ("is_me", is_me),
                    ("tc_class", tc_class), ("my_color", color),
                    ("outcome", outcome), ("analyzed", analyzed),
                    ("flagged", flagged), ("eco", eco),
                    ("end_state", end_state)):
        frag, ps = where_in(col, val)
        if frag:
            where.append(frag)
            params.extend(ps)
    if opening:
        where.append("opening LIKE ?")
        params.append(f"%{opening}%")
    if min_end_time is not None:
        where.append("end_time >= ?")
        params.append(min_end_time)
    # n_moves = game length in full moves (last analyzed ply's fullmove); NULL
    # for unanalyzed games, which have no moves rows.
    sql = ("SELECT *, (SELECT MAX(fullmove) FROM moves "
           "WHERE moves.game_id = games.id) AS n_moves FROM games")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY end_time DESC"
    return conn.execute(sql, params).fetchall()


def nth_recent_end_time(conn: sqlite3.Connection, username: str,
                        n: int) -> float | None:
    """end_time of a profile's Nth most-recent game — the cutoff for 'last N'.

    Returns None if the profile has fewer than N games (so 'last N' means all).
    """
    row = conn.execute(
        "SELECT end_time FROM games WHERE username=? AND end_time IS NOT NULL "
        "ORDER BY end_time DESC LIMIT 1 OFFSET ?", (username, max(n, 1) - 1)
    ).fetchone()
    return row["end_time"] if row else None


def unanalyzed_games(conn: sqlite3.Connection, username: str,
                     is_me: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM games WHERE username=? AND analyzed=0"
    params: list = [username]
    if is_me is not None:
        sql += " AND is_me=?"
        params.append(is_me)
    return conn.execute(sql + " ORDER BY end_time DESC", params).fetchall()


def mark_analyzed(conn: sqlite3.Connection, game_id: int) -> None:
    conn.execute("UPDATE games SET analyzed=1 WHERE id=?", (game_id,))
    conn.commit()


def store_end_state(conn: sqlite3.Connection, game_id: int) -> None:
    """Compute and store the end-of-game snapshot from the game's moves.

    The final ply's eval flipped to my POV gives the end state (winning/even/
    losing); the last move of each colour gives each player's remaining clock;
    the final position gives the piece count. No-op if the game has no analysed
    moves (or the last ply lacks an eval).
    """
    import chess

    rows = conn.execute(
        "SELECT is_me, eval_cp_after, seconds_remaining, epd_before, uci "
        "FROM moves WHERE game_id=? ORDER BY ply", (game_id,)).fetchall()
    if not rows or rows[-1]["eval_cp_after"] is None:
        return
    last = rows[-1]
    # The last mover's eval is that mover's POV; flip to mine if it wasn't me.
    my_cp = last["eval_cp_after"] if last["is_me"] else -last["eval_cp_after"]
    thr = config.WIN_THRESHOLD_CP
    state = "winning" if my_cp >= thr else "losing" if my_cp <= -thr else "even"
    mine = [r["seconds_remaining"] for r in rows
            if r["is_me"] and r["seconds_remaining"] is not None]
    opp = [r["seconds_remaining"] for r in rows
           if not r["is_me"] and r["seconds_remaining"] is not None]
    pieces = None
    try:  # piece count at the position after the last recorded move
        board = chess.Board(last["epd_before"] + " 0 1")
        board.push_uci(last["uci"])
        pieces = sum(1 for _, p in board.piece_map().items()
                     if p.piece_type != chess.KING)
    except Exception:
        pass
    conn.execute(
        "UPDATE games SET end_state=?, end_eval_cp=?, end_clock_me=?, "
        "end_clock_opp=?, end_pieces=? WHERE id=?",
        (state, my_cp, mine[-1] if mine else None,
         opp[-1] if opp else None, pieces, game_id))


def backfill_end_state(conn: sqlite3.Connection) -> int:
    """Fill the end-of-game snapshot for analysed games missing it; returns count."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM games WHERE analyzed=1 AND end_state IS NULL")]
    for gid in ids:
        store_end_state(conn, gid)
    conn.commit()
    return len(ids)


# --- moves / mistakes / grades (written by the analysis subprocess) --------
_MOVE_COLS = (
    "game_id", "ply", "fullmove", "color", "is_me", "uci", "san", "epd_before",
    "eval_cp_before", "eval_cp_after", "drop_cp", "best_uci", "phase",
    "structure", "move_type", "seconds_spent", "seconds_remaining",
    "is_long_think", "game_state",
)


def insert_moves(conn: sqlite3.Connection, rows: Sequence[dict]) -> None:
    """Bulk-insert per-move rows (keys = _MOVE_COLS)."""
    placeholders = ",".join("?" * len(_MOVE_COLS))
    conn.executemany(
        f"INSERT OR REPLACE INTO moves({','.join(_MOVE_COLS)}) "
        f"VALUES({placeholders})",
        [tuple(r.get(c) for c in _MOVE_COLS) for r in rows],
    )


def insert_mistake(conn: sqlite3.Connection, game_id: int, m: Mistake, *,
                   epd: str, is_me: int, structure: str, move_type: str,
                   phase: str, eco: str, game_state: str, ply: int) -> None:
    conn.execute(
        "INSERT INTO mistakes(game_id, ply, fen, epd, played_uci, best_pv_json, "
        "fullmove, drop_cp, is_me, structure, move_type, phase, eco, game_state, "
        "url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (game_id, ply, m.fen, epd, m.played, json.dumps(m.best_pv), m.fullmove,
         m.drop_cp, is_me, structure, move_type, phase, eco, game_state, m.url),
    )


def upsert_grade(conn: sqlite3.Connection, epd: str, grades: dict[str, int],
                 best_uci: str, eval_cp: int, depth: int, ts: float) -> None:
    conn.execute(
        "INSERT INTO grades_cache(epd, grades_json, best_uci, eval_cp, depth, "
        "created_ts) VALUES(?,?,?,?,?,?) ON CONFLICT(epd) DO UPDATE SET "
        "grades_json=excluded.grades_json, best_uci=excluded.best_uci, "
        "eval_cp=excluded.eval_cp, depth=excluded.depth",
        (epd, json.dumps(grades), best_uci, eval_cp, depth, ts),
    )


def get_grade(conn: sqlite3.Connection, epd: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM grades_cache WHERE epd=?", (epd,)).fetchone()


# --- attempts (trainer history) -------------------------------------------
def insert_attempt(conn: sqlite3.Connection, *, epd: str, source: str,
                   played_uci: str, grade: int, time_taken_s: float,
                   time_penalty: int, final_score: int, tc_class: str,
                   ts: float) -> None:
    conn.execute(
        "INSERT INTO attempts(epd, source, played_uci, grade, time_taken_s, "
        "time_penalty, final_score, tc_class, created_ts, correct) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (epd, source, played_uci, grade, time_taken_s, time_penalty,
         final_score, tc_class, ts, int(grade >= 1)),
    )
    conn.commit()


# --- import_runs (subprocess progress the UI polls) ------------------------
def start_run(conn: sqlite3.Connection, username: str, kind: str,
              total: int, ts: float) -> int:
    cur = conn.execute(
        "INSERT INTO import_runs(username, kind, status, total, done, "
        "started_ts, updated_ts) VALUES(?,?,'running',?,0,?,?)",
        (username, kind, total, ts, ts),
    )
    conn.commit()
    assert cur.lastrowid is not None  # guaranteed after a successful INSERT
    return cur.lastrowid


def update_run(conn: sqlite3.Connection, run_id: int, *, done: int | None = None,
               status: str | None = None, message: str | None = None,
               ts: float | None = None) -> None:
    sets = ["updated_ts=?"]
    params: list[str | int | float | None] = [ts]
    if done is not None:
        sets.append("done=?")
        params.append(done)
    if status is not None:
        sets.append("status=?")
        params.append(status)
    if message is not None:
        sets.append("message=?")
        params.append(message)
    params.append(run_id)
    conn.execute(f"UPDATE import_runs SET {','.join(sets)} WHERE id=?", params)
    conn.commit()


def latest_run(conn: sqlite3.Connection, username: str,
               kind: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM import_runs WHERE username=? AND kind=? "
        "ORDER BY id DESC LIMIT 1", (username, kind)).fetchone()
