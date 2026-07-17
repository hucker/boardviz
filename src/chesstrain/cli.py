"""Command-line entry points.

``chesstrain fetch``  — download the last N games (fast; network only).
``chesstrain analyze`` — run the engine batch pass over unanalyzed games
(slow; owns its own Stockfish). The Streamlit app launches ``analyze`` as a
subprocess and polls the ``import_runs`` row for progress.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, fetch
from .analysis_batch import analyze_game
from .engine import get_batch_engine


def _default_workers() -> int:
    """Use all but one core (each worker drives one 1-thread Stockfish)."""
    return max(1, (os.cpu_count() or 2) - 1)


def cmd_fetch(args: argparse.Namespace) -> None:
    conn = db.connect()
    db.init_db(conn)
    res = fetch.import_user_games(
        conn, args.user, args.n, is_me=not args.scout, tc_class=args.tc,
        on_progress=lambda c: print(f"  fetched {c}...", end="\r"),
    )
    print(f"\nfetch: {res['inserted']} new / {res['collected']} collected")
    conn.close()


def cmd_analyze(args: argparse.Namespace) -> None:
    conn = db.connect()
    db.init_db(conn)
    rows = db.unanalyzed_games(conn, args.user)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("nothing to analyze")
        return

    workers = min(args.workers or _default_workers(), len(rows))
    # Split cores across processes: fill by worker count, 1+ engine thread each.
    threads_per = max(1, (os.cpu_count() or 2) // workers)
    run_id = db.start_run(conn, args.user, "analyze", total=len(rows), ts=time.time())
    print(f"analyzing {len(rows)} games with {workers} worker(s) x "
          f"{threads_per} engine thread(s)…", flush=True)

    # Each worker thread owns its own engine + DB connection. The engine call
    # releases the GIL while Stockfish searches, so threads run in true parallel;
    # WAL lets the connections commit concurrently. Only the shared progress
    # counter and the main run-row update are serialized.
    local = threading.local()
    engines: list = []
    conns: list = []
    reg_lock = threading.Lock()
    prog_lock = threading.Lock()
    totals = {"moves": 0, "mistakes": 0, "graded": 0}
    done = 0

    def worker(row) -> None:
        nonlocal done
        if not hasattr(local, "engine"):
            local.engine = get_batch_engine(threads=threads_per)
            local.conn = db.connect()
            with reg_lock:
                engines.append(local.engine)
                conns.append(local.conn)
        counts = analyze_game(local.conn, row, local.engine)
        with prog_lock:
            done += 1
            for k in totals:
                totals[k] += counts[k]
            db.update_run(conn, run_id, done=done,
                          message=f"{totals['mistakes']} mistakes", ts=time.time())
            print(f"  [{done}/{len(rows)}] {row['url'] or row['game_uuid']}: "
                  f"{counts['mistakes']} mistakes", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in [pool.submit(worker, r) for r in rows]:
                fut.result()  # re-raise any worker exception
        db.update_run(conn, run_id, status="done", ts=time.time())
    except Exception as exc:
        db.update_run(conn, run_id, status="error", message=str(exc), ts=time.time())
        raise
    finally:
        for e in engines:
            try:
                e.quit()
            except Exception:
                pass
        for c in conns:
            c.close()
    print(f"analyze: {totals}")
    conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chesstrain")
    sub = parser.add_subparsers(dest="command", required=True)

    pf = sub.add_parser("fetch", help="download games from chess.com")
    pf.add_argument("--user", required=True)
    pf.add_argument("--n", type=int, default=100, help="number of games")
    pf.add_argument("--tc", default=None,
                    help="time-control class filter (bullet/blitz/rapid/daily)")
    pf.add_argument("--scout", action="store_true",
                    help="store as an opponent (is_me=0)")
    pf.set_defaults(func=cmd_fetch)

    pa = sub.add_parser("analyze", help="run engine analysis over unanalyzed games")
    pa.add_argument("--user", required=True)
    pa.add_argument("--limit", type=int, default=None,
                    help="cap number of games this run")
    pa.add_argument("--workers", type=int, default=None,
                    help="parallel engine workers (default: ~cores/2)")
    pa.set_defaults(func=cmd_analyze)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
