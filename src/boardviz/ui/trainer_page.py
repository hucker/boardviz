"""Trainer page: drill your mistake positions, scored on move quality.

The board and its explanatory text sit on the left; the running **Challenge**
score and this position's **Puzzle** score are boxed on the right. Each position
shows with the opponent's last move highlighted and which colour you play; you
find your move at your own pace and press Next to move on. Scores are graphical
(a correct/inaccuracy/missed badge and a per-puzzle cell bar), move quality only.
"""

from __future__ import annotations

import base64

import chess
import chess.svg
import streamlit as st

from .. import cct, grading, trainer
from ..analysis_batch import MOVE_TYPE_DEFS, PHASE_DEFS
from ..blitz_analysis import STRUCTURE_DEFS
from . import board as boardui
from . import common

_MODES = {
    "My mistakes (random)": "my_mistakes",
    "Worst mistakes first": "worst",
    "Repeat my misses": "repeat_failures",
    "Mate in 1 — deliver it": "mate1",
    "Mate in 2+ — find the key move": "mate2",
}
_MATE_MODES = ("mate1", "mate2")
# Find-difficulty (grades_cache.solve_depth) -> the min-depth filter it maps to.
# Find-difficulty (grades_cache.solve_depth) -> min-depth kept. Most positions
# are depth 3 ("obvious"), so this is really a "how much calculation" filter.
_DIFFICULTY = {"Any": None, "Beyond recaptures (≥6)": 6,
               "Calculation (≥9)": 9, "Deep (12)": 12}
# CCT scan-complexity levels: cumulative caps on cct.scan_load (forcing items to
# find, both sides). Chess-flavoured, easy→hard; "Chaos" is uncapped so the
# wildest positions stay drillable (half of them exceed 10 items).
_SCAN_LOAD = {"Quiet (≤4)": (0, 4), "Tactical (≤6)": (0, 6),
              "Sharp (≤8)": (0, 8), "Chaos (any)": None}
_DIFF_WORD = {3: "obvious", 6: "medium", 9: "hard", 12: "very hard"}
_GRADE_WORD = {2: "Best", 1: "OK", 0: "Meh", -1: "Inaccuracy", -2: "Blunder"}
_BOARD_SIZE = 600  # match the interactive board so it doesn't resize between beats


def _new_queue(conn, **filt) -> None:
    """Build a fresh drill queue from the sidebar filters (see render)."""
    cct_scope = filt.pop("cct_scope", "both")  # scan-sides scope, kept per drill
    cct_load = filt.pop("cct_load", None)  # (min, max) forcing-item band, or None
    if filt.get("mode") in _MATE_MODES:  # forced-mate drill, from mate_chances
        positions = trainer.select_mate_positions(
            conn, username=filt["username"], deep=filt["mode"] == "mate2",
            missed_only=filt.get("missed_only", False), n=filt.get("n", 20),
            source=filt.get("source"))
    elif cct_load is not None:
        # Over-fetch, then keep positions whose scan load falls in the chosen band
        # (a cheap, engine-free difficulty filter; see cct.scan_load / TRN-CCT).
        want = filt.get("n", 20)
        pool = trainer.select_positions(conn, **{**filt, "n": max(want * 8, 200)})
        lo, hi = cct_load
        positions = [p for p in pool
                     if lo <= cct.scan_load(chess.Board(p["fen"])) <= hi][:want]
    else:
        positions = trainer.select_positions(conn, **filt)
    drill = st.session_state.get("_drill_n", 0) + 1  # unique per drill, for keys
    st.session_state["_drill_n"] = drill
    st.session_state.trainer = {
        "queue": positions,
        "i": 0,
        "result": None,
        "shown_moves": set(),
        "total": 0,
        "answered": 0,
        "outcomes": [],  # each answered puzzle's score (for the challenge graphic)
        "drill": drill,
        "cct_scope": cct_scope,
    }


def _missed_positions(state: dict) -> list[dict]:
    """The positions from this drill the user got wrong — a poor move, or (on a
    CCT drill) an incomplete scan — for the unscored retry (TRN-RETRY)."""
    queue = state.get("queue", [])
    outcomes = state.get("outcomes", [])
    cct_rows = state.get("cct_rows", [])
    missed = []
    for i, pos in enumerate(queue):
        if i >= len(outcomes):
            break
        move_miss = outcomes[i] < 1.0
        row = cct_rows[i] if i < len(cct_rows) else None
        scan_miss = row is not None and any(v < 1.0 for v in row.values())
        if move_miss or scan_miss:
            missed.append(pos)
    return missed


def _start_practice(state: dict, missed: list[dict]) -> None:
    """Begin an unscored practice pass over ``missed`` (TRN-RETRY). Same board and
    scan-side scope as the drill it came from; records nothing."""
    drill = st.session_state.get("_drill_n", 0) + 1
    st.session_state["_drill_n"] = drill
    st.session_state.trainer = {
        "queue": missed,
        "i": 0,
        "result": None,
        "shown_moves": set(),
        "total": 0,
        "answered": 0,
        "outcomes": [],
        "drill": drill,
        "cct_scope": state.get("cct_scope", "both"),
        "practice": True,
    }


def _intro_for(pos: dict) -> dict:
    """The board's pre-move context: the opponent's last move highlighted (None on
    a game's first move). No countdown — the drill is self-paced."""
    return {"delayMs": 0, "lastMove": pos.get("opp_move")}


def _advance(state: dict) -> None:
    """Move the drill to the next position (reset per-position state)."""
    state["i"] += 1
    state["result"] = None
    state["shown_moves"] = set()


def _credit_played_move(board: chess.Board, played_uci: str, marked: dict) -> dict:
    """Return ``marked`` with the move you actually played credited as a found
    check/capture, even if you forgot to mark it — playing a forcing move proves
    you saw it (TRN-CCT). Matches on the from/to squares (ignoring promotion)."""
    out = {k: list(v) for k, v in marked.items()}
    move = chess.Move.from_uci(played_uci)
    if move not in board.legal_moves:
        return out
    key = played_uci[:4]
    for cat, is_kind in (("captures", board.is_capture), ("checks", board.gives_check)):
        if is_kind(move) and all(m[:4] != key for m in out.get(cat, [])):
            out.setdefault(cat, []).append(played_uci)
    return out


def _commit_move(
    conn, pos: dict, board: chess.Board, state: dict, played: dict
) -> None:
    """Score and record an answered move, then advance to review (shared by the
    plain puzzle and the CCT beat)."""
    move = chess.Move.from_uci(played["uci"])
    if move not in board.legal_moves:
        return
    elapsed = (
        played["ms"] / 1000.0
    )  # browser-measured think time (recorded, not scored)
    scored = grading.score_attempt(pos["grades"], played["uci"])
    scored.update(uci=played["uci"], san=board.san(move), elapsed=elapsed)
    cct_row = None  # per-category fractions for the progress grid (None = non-CCT)
    if "marked" in played:  # a CCT beat also returns the arrows/rings you drew
        # A forcing move you play but forgot to mark still counts — you saw it.
        marked = _credit_played_move(board, played["uci"], played["marked"])
        scored["marked"] = marked
        (scored["cct_complete"], scored["cct_found"],
         scored["cct_avail"], scored["cct_cells"]) = _accumulate_cct(
            state, board, marked)
        pos_score = _cct_position_score(scored["cct_cells"], scored["final_score"])
        scored["cct_pos_score"] = pos_score  # this position, out of 4
        state["cct_score"] = state.get("cct_score", 0) + pos_score
        state["cct_max"] = state.get("cct_max", 0) + 4
        cells = scored["cct_cells"]
        cct_row = {c: sum(cells[s][c]["frac"] for s in cells) / len(cells)
                   for c in _CCT_CATS}
    state["result"] = scored
    state["total"] = state.get("total", 0) + scored["final_score"]
    state["answered"] = state.get("answered", 0) + 1
    state.setdefault("outcomes", []).append(scored["final_score"])
    # Kept index-aligned with outcomes (None for a non-CCT beat) so the grid's
    # category cells land in the same column as their Move cell.
    state.setdefault("cct_rows", []).append(cct_row)
    if not state.get("practice"):  # unscored retry pass records nothing (TRN-RETRY)
        trainer.record_attempt(
            conn,
            epd=pos["epd"],
            source="trainer",
            played_uci=played["uci"],
            grade=scored["grade"],
            elapsed_s=elapsed,
            time_penalty=0,
            final_score=scored["final_score"],
            tc_class=pos["tc_class"],
        )
    st.rerun()


def _advance_controls(state: dict) -> None:
    """A Next button to move on — shared by the puzzle and mate reviews."""
    if st.button("Next ▶", type="primary", key=f"next-{state['i']}"):
        _advance(state)
        st.rerun()


def _pv_san(board: chess.Board, pv: list[str]) -> str:
    """Render a UCI line to SAN from ``board`` (best-effort, stops on a bad move)."""
    b, out = board.copy(stack=False), []
    for u in pv:
        try:
            m = chess.Move.from_uci(u)
            out.append(b.san(m))
            b.push(m)
        except (ValueError, AssertionError):
            break
    return " ".join(out)


def _mate_correct(board: chess.Board, played_uci: str, pos: dict) -> bool:
    """Whether the played move solves the mate puzzle: for M1, any move that
    delivers checkmate; for a deeper mate, the stored forcing key move."""
    if pos["distance"] == 1:
        after = board.copy(stack=False)
        after.push(chess.Move.from_uci(played_uci))
        return after.is_checkmate()
    return played_uci == pos["key_uci"]


def _commit_mate(conn, pos: dict, board: chess.Board, state: dict,
                 played: dict) -> None:
    """Score a mate answer (1 = solved, 0 = missed) and advance to review."""
    move = chess.Move.from_uci(played["uci"])
    if move not in board.legal_moves:
        return
    elapsed = played["ms"] / 1000.0
    correct = _mate_correct(board, played["uci"], pos)
    final = 1.0 if correct else 0.0
    state["result"] = {
        "uci": played["uci"], "san": board.san(move), "elapsed": elapsed,
        "correct": correct, "final_score": final, "grade": 2 if correct else -2,
    }
    state["total"] = state.get("total", 0) + final
    state["answered"] = state.get("answered", 0) + 1
    state.setdefault("outcomes", []).append(final)
    state.setdefault("cct_rows", []).append(None)  # keep index-aligned (no CCT)
    if not state.get("practice"):  # unscored retry pass records nothing (TRN-RETRY)
        trainer.record_attempt(
            conn, epd=pos["epd"], source="mate", played_uci=played["uci"],
            grade=2 if correct else -2, elapsed_s=elapsed, time_penalty=0,
            final_score=final, tc_class=pos["tc_class"])
    st.rerun()


def _mate_puzzle(conn, pos: dict, board: chess.Board, state: dict,
                 left, right) -> None:
    """The mate drill: you had a forced mate here — find the move."""
    with left:
        played = boardui.board_input(
            board, key=f"mate-board-{state['i']}", intro=_intro_for(pos))
    with right:
        st.caption("Your score for this move appears here once you play.")
    if played:
        _commit_mate(conn, pos, board, state, played)


def _mate_review(pos: dict, board: chess.Board, state: dict, res: dict,
                 left, right) -> None:
    """Answered a mate: show the mating key move (and your move if you missed)."""
    key, d = pos["key_uci"], pos["distance"]
    km = chess.Move.from_uci(key)
    arrows = [chess.svg.Arrow(km.from_square, km.to_square, color="#2c7")]
    if not res["correct"] and res["uci"] != key:
        pm = chess.Move.from_uci(res["uci"])
        arrows.append(chess.svg.Arrow(pm.from_square, pm.to_square, color="#c33"))
    with left:
        boardui.show_board(board, size=_BOARD_SIZE, arrows=arrows,
                           orientation=board.turn)
        st.caption("Green = the mating move"
                   + ("" if res["correct"] else " · red = your move"))
    with right:
        _svg_img(_result_badge_svg(res["final_score"]), "result")
        key_san = board.san(km)
        if res["correct"]:
            st.success(f"✓ **Checkmate!** {res['san']}" if d == 1
                       else f"✓ **Forces mate in {d}** — {res['san']}")
        else:
            st.error(f"Missed it — the key move was **{key_san}**"
                     + ("." if d == 1 else f", forcing mate in {d}."))
        if d > 1 and pos.get("mate_pv"):
            st.caption("Forced line: " + _pv_san(board, pos["mate_pv"]))
        if pos.get("motif"):
            st.caption(f"Motif: {pos['motif']}")
        if pos.get("url"):
            st.markdown(f"[Open game]({pos['url']})")
        _advance_controls(state)


def _cct_legend() -> None:
    """A popover explaining the CCT board's layers, colours and gestures."""
    with st.popover("🎨 Colour key", help="What the marks on the board mean"):
        st.markdown(
            "**Work one layer at a time** with the board tabs — "
            ":blue-badge[Checks] :orange-badge[Captures] "
            ":red-badge[Loose] — for you *and* your opponent."
        )
        st.markdown(
            "**Checks / Captures**  \nclick a piece, then its target square (an arrow)."
        )
        st.markdown(
            "**Loose pieces** — a piece winnable **right now**: hanging, or a "
            "favourable trade (the cheapest attacker is worth less). A quick "
            "*material* check — **not** an engine run, so no discovered attacks, "
            "forks or deep tactics. Click the piece to ring it."
        )
        st.markdown("**Side — the line**  \nsolid = you · dashed = the opponent")
        st.markdown(
            "**A correct mark gets a ✓**  \na wrong one buzzes and won't stick, "
            "with a note on why — identify them, don't guess."
        )
        st.markdown(
            "**Checks that capture** add the capture for you, and **mutual "
            "captures** mark both sides — but a capture is never auto-marked as a "
            "check: spotting that is the point."
        )
        st.markdown(
            "**Play your move** by dragging a piece (or Shift-click its target)."
        )


def _cct_beat(conn, pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """Both-ways CCT: scan checks/captures/loose pieces for the scoped side(s),
    then play your move on the same board (drag or Shift-click)."""
    scope = state.get("cct_scope", "both")
    scan = cct.scoped_scan(cct.scan_both(board), scope)
    with left:
        played = boardui.board_scan(
            board, scan, key=f"cct-{state['i']}", last_move=pos.get("opp_move"),
            scope=scope,
        )
    with right:
        _cct_legend()
        st.caption("Each mark is graded ✓/✗; your tally and what you missed "
                   "show here after you move.")
    if played:
        _commit_move(conn, pos, board, state, played)


def _side_line(board: chess.Board) -> str:
    """A bold 'which colour am I' label — the board orientation alone can be
    ambiguous, especially in sparse endgames (TRN-INTRO)."""
    chip = "⚪" if board.turn else "⚫"
    return f"{chip} Playing as **{'White' if board.turn else 'Black'}**"


_OK_GREEN = "#16a34a"  # correct / best move
_MID_AMBER = "#f59e0b"  # inaccuracy (half credit)
_BAD_RED = "#ef4444"  # missed / blunder
_REMAIN = "#e5e7eb"  # a puzzle not yet reached


def _svg_img(svg: str, alt: str = "") -> None:
    """Render inline SVG as a data-URI image (re-renders every run, unlike an
    iframe component) — the shared way the trainer draws its score graphics."""
    b64 = base64.b64encode(svg.encode()).decode()
    st.markdown(f"![{alt}](data:image/svg+xml;base64,{b64})")


def _cell_color(score: float) -> str:
    """Outcome colour for one answered puzzle (correct / inaccuracy / missed)."""
    return _OK_GREEN if score >= 1 else _MID_AMBER if score >= 0.5 else _BAD_RED


def _result_badge_svg(score: float) -> str:
    """A single big result chip for the puzzle just answered — a graphical
    correct/inaccuracy/missed, in place of a bare '+1' number."""
    if score >= 1:
        col, glyph, word = _OK_GREEN, "✓", "Correct"
    elif score >= 0.5:
        col, glyph, word = _MID_AMBER, "≈", "Inaccuracy"
    else:
        col, glyph, word = _BAD_RED, "✗", "Missed"
    vw, vh = 210, 42
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}" height="{vh}" '
        f'viewBox="0 0 {vw} {vh}" font-family="sans-serif">'
        f'<rect width="{vw}" height="{vh}" rx="9" fill="{col}"/>'
        f'<text x="15" y="29" font-size="21" font-weight="700" fill="#fff">{glyph}</text>'
        f'<text x="44" y="28" font-size="16" font-weight="700" fill="#fff">{word}</text>'
        "</svg>"
    )


def _frac_color(frac: float) -> str:
    """Colour a category cell by fraction complete: green all, amber some, red none."""
    return _OK_GREEN if frac >= 0.999 else _MID_AMBER if frac > 0 else _BAD_RED


def _progress_grid_svg(outcomes: list[float], cct_rows: list[dict], n: int) -> str:
    """The drill's running score as a grid — one column per position, green
    (all/best), amber (partial/inaccuracy), red (none/missed), grey (still to
    come). Always a **Move** row; on a CCT drill a row per category (checks /
    captures / loose) beneath it, so you can see which one you keep dropping."""
    # cct_rows is index-aligned with outcomes (None where a position wasn't a
    # CCT beat), so a category cell shares its column with that position's Move.
    rows = [("Move", "#374151", list(outcomes), _cell_color)]
    if any(r is not None for r in cct_rows):
        for c in _CCT_CATS:
            vals = [(r[c] if r is not None else None) for r in cct_rows]
            rows.append((_CAT_LABEL[c].capitalize(), _CAT_COLOR[c], vals,
                         _frac_color))
    vw = 330
    lx, cx0, gap, top, rowh, ch = 8, 58, 2, 24, 15, 11
    cw = (vw - 8 - cx0 - (n - 1) * gap) / n if n else 0
    vh = top + len(rows) * rowh + 6
    correct = sum(1 for s in outcomes if s >= 1)
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}" height="{vh}" '
         f'viewBox="0 0 {vw} {vh}" font-family="sans-serif">',
         f'<rect width="{vw}" height="{vh}" rx="6" fill="#ffffff"/>',
         '<text x="8" y="15" font-size="11" font-weight="700" '
         'fill="#374151">Drill progress</text>',
         f'<text x="{vw - 8}" y="15" font-size="11" font-weight="700" '
         f'text-anchor="end" fill="{_OK_GREEN}">{correct}/{n}</text>']
    for ri, (label, color, vals, cfn) in enumerate(rows):
        ry = top + ri * rowh
        p.append(f'<text x="{lx}" y="{ry + 9}" font-size="9" font-weight="700" '
                 f'fill="{color}">{label}</text>')
        for i in range(n):
            cx = cx0 + i * (cw + gap)
            v = vals[i] if i < len(vals) else None
            col = cfn(v) if v is not None else _REMAIN
            p.append(f'<rect x="{cx:.1f}" y="{ry}" width="{cw:.1f}" height="{ch}" '
                     f'rx="2" fill="{col}"/>')
    p.append("</svg>")
    return "".join(p)


def _cct_counts(board: chess.Board, marked: dict, scan: dict) -> dict:
    """How many of your marks were correct, per side (see board_scan / _cct_beat).

    ``marked`` is ``{"checks": [uci], "captures": [uci], "threats": [square]}`` —
    what you marked in each layer. A move's side is the colour of the piece it
    starts on; a threat's side is whose piece it lands on (an enemy piece = your
    threat, your own = the opponent's). Promotions match on the from/to squares.
    """
    found = {
        "me": {"checks": 0, "captures": 0, "threats": 0},
        "opp": {"checks": 0, "captures": 0, "threats": 0},
    }
    truth = {
        s: {c: {u[:4] for u in scan[s][c]} for c in ("checks", "captures")}
        for s in ("me", "opp")
    }
    for cat in ("checks", "captures"):
        for uci in marked.get(cat, []):
            piece = board.piece_at(chess.parse_square(uci[:2]))
            if piece is None:
                continue
            side = "me" if piece.color == board.turn else "opp"
            if uci[:4] in truth[side][cat]:
                found[side][cat] += 1
    for sq in marked.get("threats", []):
        piece = board.piece_at(chess.parse_square(sq))
        if piece is None:
            continue
        side = "opp" if piece.color == board.turn else "me"
        if sq in scan[side]["threats"]:
            found[side]["threats"] += 1
    return found


_CCT_CATS = ("checks", "captures", "threats")


def _zero_cct() -> dict:
    """A zeroed found/available accumulator: {side: {category: 0}}."""
    return {s: {c: 0 for c in _CCT_CATS} for s in ("me", "opp")}


def _scope_sides(scope: str) -> tuple[str, ...]:
    """The sides a drill scores under its scan-sides scope (both / me / opp)."""
    return ("me", "opp") if scope == "both" else (scope,)


def _cct_cells(found: dict, avail: dict, scope: str) -> dict:
    """Per in-scope (side, category) completeness for the heat map and score.

    Each cell is ``{"avail", "found", "frac", "complete", "state"}`` where
    *state* is ``found`` (had items, got them all), ``missed`` (had items, missed
    some), or ``none`` (nothing to find — trivially complete). Empty is the
    default: a bucket with nothing in it needs no action and is complete.
    """
    cells: dict = {}
    for s in _scope_sides(scope):
        cells[s] = {}
        for c in _CCT_CATS:
            a, f = avail[s][c], found[s][c]
            if a == 0:
                complete, frac, st = True, 1.0, "none"
            else:
                complete, frac = f == a, f / a
                st = "found" if complete else "missed"
            cells[s][c] = {"avail": a, "found": f, "frac": frac,
                           "complete": complete, "state": st}
    return cells


def _accumulate_cct(state: dict, board: chess.Board, marked: dict) -> tuple:
    """Add a CCT position's result to the drill's running tally.

    Updates ``state['cct_found']`` / ``state['cct_avail']`` (the running
    real-item totals) and returns ``(complete, found, avail, cells)`` for *this*
    position. *complete* = every in-scope bucket with items had them all found
    (an empty bucket is trivially complete). ``cells`` drives the per-position
    heat map. The scan-sides scope (``state['cct_scope']``) limits which sides
    count.
    """
    scope = state.get("cct_scope", "both")
    scan = cct.scoped_scan(cct.scan_both(board), scope)
    found = _cct_counts(board, marked, scan)
    avail = {s: {c: len(scan[s][c]) for c in _CCT_CATS} for s in ("me", "opp")}
    run_f = state.setdefault("cct_found", _zero_cct())
    run_a = state.setdefault("cct_avail", _zero_cct())
    for s in ("me", "opp"):
        for c in _CCT_CATS:
            run_f[s][c] += found[s][c]
            run_a[s][c] += avail[s][c]
    cells = _cct_cells(found, avail, scope)
    complete = all(cell["complete"] for row in cells.values()
                   for cell in row.values())
    return complete, found, avail, cells


def _cct_position_score(cells: dict, move_score: float) -> float:
    """A CCT position's score, out of 4: for each category (checks / captures /
    loose) the mean over in-scope sides of the fraction found — so a partial scan
    earns partial credit — plus the move score (0 / 0.5 / 1). A category with
    nothing to find is trivially complete (worth its full share)."""
    cats = 0.0
    for c in _CCT_CATS:
        fracs = [cells[s][c]["frac"] for s in cells]
        cats += sum(fracs) / len(fracs) if fracs else 1.0
    return cats + move_score


def _san_safe(board: chess.Board, uci: str) -> str:
    """SAN for a UCI move, falling back to the raw UCI on any parse error."""
    try:
        return board.san(chess.Move.from_uci(uci))
    except (ValueError, AssertionError):
        return uci


def _threat_label(board: chess.Board, sq_name: str) -> str:
    """A threatened piece as algebra: piece letter + square (pawns bare) — e.g. Ne5."""
    piece = board.piece_at(chess.parse_square(sq_name))
    if piece is None:
        return sq_name
    letter = ("" if piece.piece_type == chess.PAWN
              else chess.piece_symbol(piece.piece_type).upper())
    return f"{letter}{sq_name}"


def _cct_missed(board: chess.Board, marked: dict, scan: dict) -> dict:
    """Per-side lists of the CCT items you did NOT find, as algebra (SAN for
    checks/captures, piece+square for threats). Deduped by from/to."""
    hit = {c: {m[:4] for m in marked.get(c, [])} for c in ("checks", "captures")}
    hit_threats = set(marked.get("threats", []))
    # The opponent's moves are only legal on the null-moved board (their turn), so
    # their SAN must be taken there — otherwise disambiguation and check suffixes
    # come out wrong (two rook captures both read "Rxh2" instead of e.g. R2xh2).
    opp_board = board
    if not board.is_check():
        opp_board = board.copy(stack=False)
        opp_board.push(chess.Move.null())
    out: dict = {"me": {}, "opp": {}}
    for side in ("me", "opp"):
        san_board = board if side == "me" else opp_board
        for cat in ("checks", "captures"):
            seen: set[str] = set()
            labels = []
            for uci in sorted(scan[side][cat]):
                k = uci[:4]
                if k in hit[cat] or k in seen:
                    continue
                seen.add(k)
                labels.append(_san_safe(san_board, uci))
            out[side][cat] = labels
        out[side]["threats"] = [_threat_label(board, sq)
                                for sq in sorted(scan[side]["threats"])
                                if sq not in hit_threats]
    return out


def _cct_missed_table(board: chess.Board, marked: dict, scan: dict) -> None:
    """A compact table of the missed CCT items in algebra — rows are the three
    layers, columns You / Opponent. Renders nothing when everything was found."""
    missed = _cct_missed(board, marked, scan)
    rows = []
    for cat, label in (("checks", "Checks"), ("captures", "Captures"),
                       ("threats", "Loose")):
        you, opp = missed["me"][cat], missed["opp"][cat]
        if you or opp:
            rows.append((label, ", ".join(you) or "—", ", ".join(opp) or "—"))
    if not rows:
        return
    md = ["| Missed | You | Opponent |", "|:--|:--|:--|"]
    md += [f"| **{c}** | {y} | {o} |" for c, y, o in rows]
    st.markdown("\n".join(md))


_CAT_COLOR = {"checks": "#3b82f6", "captures": "#f59e0b", "threats": "#ef4444"}
# The internal category key stays "threats" (data/schema stability); the user
# sees "loose" — pieces winnable right now, which is what the layer computes.
_CAT_LABEL = {"checks": "checks", "captures": "captures", "threats": "loose"}


def _bar_svg(x: int, y: int, w: int, h: int, frac: float, color: str) -> str:
    """A rounded progress bar (grey track + coloured fill) as SVG markup."""
    r = h / 2
    fw = max(0, min(w, round(w * frac)))
    out = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="#e5e7eb"/>'
    if fw:
        out += f'<rect x="{x}" y="{y}" width="{fw}" height="{h}" rx="{r}" fill="{color}"/>'
    return out


def _cct_scoreboard_svg(found: dict, avail: dict, *, title: str = "CCT scan tally",
                        note: str | None = None, note_color: str = "#6b7280",
                        scale: float = 1.0) -> str:
    """A scoreboard image: six per-category bars (You/Opp × checks/captures/
    threats) plus a total, each found / available. Self-contained (own white
    card). ``note`` is right-aligned in the title row (e.g. the m/n score,
    ``note_color`` green on a perfect run); ``scale`` sizes the rendered image
    (viewBox is fixed, so it stays crisp)."""
    vw, vh = 330, 108
    colx = {"checks": 46, "captures": 142, "threats": 238}
    barw = 54
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{round(vw * scale)}" '
         f'height="{round(vh * scale)}" viewBox="0 0 {vw} {vh}" '
         f'font-family="sans-serif">',
         f'<rect width="{vw}" height="{vh}" rx="6" fill="#ffffff"/>',
         f'<text x="8" y="15" font-size="11" font-weight="700" '
         f'fill="#374151">{title}</text>']
    if note:
        p.append(f'<text x="{vw - 8}" y="15" font-size="11" font-weight="700" '
                 f'text-anchor="end" fill="{note_color}">{note}</text>')
    for c in _CCT_CATS:
        p.append(f'<text x="{colx[c]}" y="30" font-size="9" font-weight="700" '
                 f'fill="{_CAT_COLOR[c]}">{_CAT_LABEL[c]}</text>')
    # A side with nothing found *and* nothing available is out of the drill's
    # scan scope (or trivially empty) — its row would read 0/0 thrice; hide it.
    rows = [(s, lbl) for s, lbl in (("me", "You"), ("opp", "Opp"))
            if any(found[s].values()) or any(avail[s].values())] or [("me", "You")]
    for i, (side, label) in enumerate(rows):
        ry = 42 + i * 24
        p.append(f'<text x="8" y="{ry + 8}" font-size="10" fill="#555">{label}</text>')
        for c in _CCT_CATS:
            f, a = found[side][c], avail[side][c]
            p.append(_bar_svg(colx[c], ry + 1, barw, 8, f / a if a else 0, _CAT_COLOR[c]))
            p.append(f'<text x="{colx[c] + barw + 4}" y="{ry + 8}" font-size="9" '
                     f'fill="#555">{f}/{a}</text>')
    tf = sum(found[s][c] for s in ("me", "opp") for c in _CCT_CATS)
    ta = sum(avail[s][c] for s in ("me", "opp") for c in _CCT_CATS)
    p.append('<text x="8" y="98" font-size="10" font-weight="700" '
             'fill="#374151">Total</text>')
    p.append(_bar_svg(46, 91, 200, 9, tf / ta if ta else 0, "#16a34a"))
    p.append(f'<text x="252" y="98" font-size="10" font-weight="700" '
             f'fill="#16a34a">{tf}/{ta}</text>')
    p.append("</svg>")
    return "".join(p)


def _cct_scoreboard(found: dict, avail: dict, *, title: str = "CCT scan tally",
                    note: str | None = None, note_color: str = "#6b7280",
                    scale: float = 1.0) -> None:
    """Render a CCT scoreboard as a compact inline SVG graphic."""
    svg = _cct_scoreboard_svg(found, avail, title=title, note=note,
                              note_color=note_color, scale=scale)
    b64 = base64.b64encode(svg.encode()).decode()
    st.markdown(f"![{title}](data:image/svg+xml;base64,{b64})")


# Heat-map cell fills + glyphs per state.
_CELL_STYLE = {
    "found":  ("#16a34a", "✓"),   # had items, found them all
    "missed": ("#ef4444", "✗"),   # had items, missed some
    "none":   ("#d1d5db", "·"),   # nothing to find — neutral, trivially complete
}
_MOVE_STYLE = {1.0: ("#16a34a", "✓"), 0.5: ("#f59e0b", "~"), 0.0: ("#ef4444", "✗")}


def _cell_svg(x: int, y: int, fill: str, glyph: str, sz: int = 18) -> str:
    """One rounded heat-map cell: a coloured square with a centred glyph."""
    return (f'<rect x="{x}" y="{y}" width="{sz}" height="{sz}" rx="4" fill="{fill}"/>'
            f'<text x="{x + sz / 2}" y="{y + sz / 2 + 0.5}" font-size="12" '
            f'font-weight="700" fill="#ffffff" text-anchor="middle" '
            f'dominant-baseline="central">{glyph}</text>')


def _cct_heatmap_svg(cells: dict, move_score: float, *, note: str | None = None,
                     note_color: str = "#6b7280", scale: float = 1.0) -> str:
    """A per-position heat map: one cell per in-scope side × category, plus the
    move, green when the bucket is complete and red when something was missed,
    falsely cleared, or left unscanned (TRN-CCT)."""
    sides = [s for s in ("me", "opp") if s in cells]
    colx = {"checks": 70, "captures": 138, "threats": 206}
    vw = 250
    vh = 34 + len(sides) * 24 + 26
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{round(vw * scale)}" '
         f'height="{round(vh * scale)}" viewBox="0 0 {vw} {vh}" '
         f'font-family="sans-serif">',
         f'<rect width="{vw}" height="{vh}" rx="6" fill="#ffffff"/>',
         '<text x="8" y="14" font-size="11" font-weight="700" '
         'fill="#374151">This position</text>']
    if note:
        p.append(f'<text x="{vw - 8}" y="14" font-size="11" font-weight="700" '
                 f'text-anchor="end" fill="{note_color}">{note}</text>')
    for c in _CCT_CATS:
        p.append(f'<text x="{colx[c]}" y="30" font-size="9" font-weight="700" '
                 f'fill="{_CAT_COLOR[c]}">{_CAT_LABEL[c]}</text>')
    for i, s in enumerate(sides):
        ry = 36 + i * 24
        label = "You" if s == "me" else "Opp"
        p.append(f'<text x="8" y="{ry + 14}" font-size="10" fill="#555">{label}</text>')
        for c in _CCT_CATS:
            fill, glyph = _CELL_STYLE[cells[s][c]["state"]]
            p.append(_cell_svg(colx[c], ry, fill, glyph))
    my = 36 + len(sides) * 24
    mfill, mglyph = _MOVE_STYLE.get(move_score, ("#f59e0b", "~"))
    p.append(f'<text x="8" y="{my + 14}" font-size="10" font-weight="700" '
             f'fill="#374151">Move</text>')
    p.append(_cell_svg(colx["checks"], my, mfill, mglyph))
    p.append("</svg>")
    return "".join(p)


def _cct_heatmap(cells: dict, move_score: float, *, note: str | None = None,
                 note_color: str = "#6b7280") -> None:
    """Render the per-position CCT heat map as an inline SVG graphic."""
    svg = _cct_heatmap_svg(cells, move_score, note=note, note_color=note_color)
    b64 = base64.b64encode(svg.encode()).decode()
    st.markdown(f"![position heat map](data:image/svg+xml;base64,{b64})")


def _puzzle(conn, pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """The interactive puzzle: play your move at your own pace."""
    with left:
        played = boardui.board_input(
            board, key=f"trainer-board-{state['i']}", intro=_intro_for(pos)
        )
    with right:
        st.caption("Your score for this move appears here once you play.")
    if played:
        _commit_move(conn, pos, board, state, played)


def _review(
    pos: dict, board: chess.Board, state: dict, res: dict, left, right
) -> None:
    """Answered: the best move is always drawn in green — so you can see the move
    you should have played — and your move is drawn too: the same green when you
    played the best, otherwise black. Any move you click to compare is grey."""
    grades = pos["grades"]
    best, played = pos["best_uci"], res["uci"]
    plus = sorted(
        ((u, g) for u, g in grades.items() if g >= 1), key=lambda ug: (-ug[1], ug[0])
    )
    shown = state.setdefault("shown_moves", set())

    def _arrow(uci: str, color: str):
        m = chess.Move.from_uci(uci)
        return chess.svg.Arrow(m.from_square, m.to_square, color=color)

    arrows = []
    for u in shown:  # moves you clicked to compare — grey, under the rest
        if u != best and u != played:
            arrows.append(_arrow(u, "#9ca3af"))
    if best != played:  # the correct move you missed — always bright green
        arrows.append(_arrow(best, "#22c55e"))
    # your move, drawn on top: the same green if it WAS the best, else black
    arrows.append(_arrow(played, "#22c55e" if played == best else "#1c1c1c"))

    marked = res.get("marked")
    cct_scan = (cct.scoped_scan(cct.scan_both(board),
                                state.get("cct_scope", "both"))
                if marked is not None else None)
    with left:
        if cct_scan is not None:  # CCT beat: reveal the both-ways scan, by layer
            compare = [u for u in shown if u != best and u != played]
            # Fold the compare set into the key so toggling a button remounts the
            # component (a data-only change may not re-render it); the grey arrow
            # then appears. (Cost: the layer resets to Checks on toggle.)
            boardui.board_scan(
                board,
                cct_scan,
                key=f"cct-rev-{state['i']}-{'.'.join(sorted(compare))}",
                reveal=True,
                played=played,
                marked=marked,
                best=best,
                compare=compare,
                scope=state.get("cct_scope", "both"),
            )
            _cct_legend()
            st.caption(
                "Flip layers with the board tabs — **bright = you missed "
                "it**, faded = found · solid = you, dashed = opponent · "
                ":green[green = the best move] · grey = a compared move."
            )
        else:
            boardui.show_board(
                board, size=_BOARD_SIZE, arrows=arrows, orientation=board.turn
            )
            st.caption(
                "Green = the best move — you played it · grey = a compared move."
                if played == best
                else "Green = the best move · black = your move · "
                "grey = a compared move."
            )
    with right:
        _svg_img(_result_badge_svg(res["final_score"]), "result")
        if cct_scan is not None:
            got_move = res["grade"] >= 1  # a good/best move — required for the effects
            if not res.get("celebrated"):  # once per position, on entering review
                res["celebrated"] = True
                if got_move and res.get("cct_complete"):
                    st.balloons()
            cells = res.get("cct_cells", {})
            missed = sum(cell["avail"] - cell["found"] for row in cells.values()
                         for cell in row.values() if cell["state"] == "missed")
            if res.get("cct_complete"):
                if got_move:
                    st.success("✨ Clean scan — and the right move. Nailed it!")
                else:
                    st.info("Clean scan — now find the best move for the celebration.")
            else:
                st.warning(f"This position: missed {missed} item(s) — "
                           "shown bright on the board.")
                _cct_missed_table(board, marked or {}, cct_scan)
            if cells:  # this puzzle's own heat map, beside the score
                ps = res.get("cct_pos_score", 0)
                _cct_heatmap(cells, res["final_score"], note=f"{ps:g}/4",
                             note_color="#16a34a" if ps == 4 else "#6b7280")
        # Make the move's quality unmissable when you didn't find the best move.
        best_san = board.san(chess.Move.from_uci(best))
        if played == best:
            st.success(f"✓ **Best move** — {res['san']}")
        else:
            word = _GRADE_WORD.get(res["grade"], f"{res['grade']:+d}")
            box = st.error if res["grade"] <= -1 else st.warning
            box(
                f"Your move **{res['san']}** was **{word}** ({res['grade']:+d}) "
                f"— best was **{best_san}** (+2)."
            )
        st.write(grading.win_loss_readout(pos["eval_cp"]))

        # Best and your move are always on the board; the other good moves are
        # toggle buttons that add/remove their grey arrow.
        others = [(u, g) for u, g in plus if u != best and u != played]
        if others:
            st.caption("Compare another good move (grey — click to toggle):")
            for u, g in others:
                on = u in shown
                word = _GRADE_WORD.get(g, f"{g:+d}")
                san = board.san(chess.Move.from_uci(u))
                if st.button(f"{'☑' if on else '☐'} {word} {g:+d} — {san}",
                             key=f"opt-{state['i']}-{u}", width="stretch"):
                    shown.discard(u) if on else shown.add(u)
                    st.rerun()

        _advance_controls(state)


def _over_board(conn, pos: dict, board: chess.Board, i: int, n: int,
                answered: bool, cct_on: bool) -> None:
    """The compact header above the board: which position, which colour you play
    (orientation alone is ambiguous in sparse endgames — TRN-INTRO), what to do,
    and the game source. The colour line is bold text (not a heading, to stay
    tight and drop the anchor icon) and carries a ``?`` tooltip with the full game
    info — matchup, date, TC, opening, game link, FEN/EPD (TRN-CONTEXT)."""
    if pos.get("mate"):
        tags = (f"**Mate in {pos['distance']}**"
                + (f" · {pos['motif']}" if pos.get("motif") else ""))
    else:
        diff = _DIFF_WORD.get(pos.get("solve_depth"))
        tags = (f"{pos['structure']} · {pos['move_type']} · {pos['phase']}"
                f" · {pos['tc_class']}" + (f" · {diff} find" if diff else ""))
    st.caption(f"Position {i + 1} / {n} · {tags}")
    # Keep the visible line clean ("⚫ Playing as Black"); the action and the full
    # game info (source, link, FEN/EPD) live in the ``?`` tooltip.
    info = common.game_info_help(
        conn, fen=pos["fen"], url=pos.get("url"), epd=pos.get("epd"),
        tc_class=pos.get("tc_class"))
    action = _play_instruction(pos, board, answered, cct_on)
    tooltip = f"{action}\n\n{info}" if action else info
    st.markdown(_side_line(board), help=tooltip)


def _play_instruction(pos: dict, board: chess.Board, answered: bool,
                      cct_on: bool) -> str | None:
    """The action for the header's colour line (no 'to move' prefix — the colour
    line already names the side). None once answered — the board's own legend then
    explains the review."""
    if answered:
        return None
    if pos.get("mate"):
        d = pos["distance"]
        task = ("deliver checkmate" if d == 1
                else f"play the move that forces mate in {d}")
        return f"{task} (you had a forced mate here). No hints."
    if cct_on:
        return ("mark the checks / captures / threats both ways, then play your "
                "move (drag, or Shift-click) — no hints.")
    return "play the move you think is best. No hints."


def _challenge_box(state: dict, n: int) -> None:
    """The drill's running score (right column, top): the correct/inaccuracy/
    missed/to-go progress cell bar for **every** drill (TRN-TALLY), plus — on a
    CCT drill — the six-bar found/available tally beneath it (TRN-CCT). The bar
    stays put all drill; the CCT tally only appears once you've answered one."""
    _svg_img(_progress_grid_svg(state.get("outcomes", []),
                                state.get("cct_rows", []), n), "drill progress")
    if state.get("cct_avail"):
        cs, cm = state.get("cct_score", 0), state.get("cct_max", 0)
        perfect = cm > 0 and cs == cm
        _cct_scoreboard(state["cct_found"], state["cct_avail"],
                        title="CCT — drill total", note=f"{cs:g}/{cm:g}",
                        note_color=_OK_GREEN if perfect else "#6b7280")


def render() -> None:
    st.header("🎯 Trainer")
    conn = common.get_conn()
    username = common.profile_picker(conn)
    if username is None:
        st.info(
            "Analyze some games first — the trainer drills a profile's own "
            "mistake positions."
        )
        return

    def _pills(label, values, help=None):  # multi-select; [] (empty) means all
        return st.pills(label, list(values), selection_mode="multi",
                        help=help) or None

    with st.sidebar:
        st.subheader("Drill setup")
        start_slot = st.container()  # Start button renders here (top); filt built below
        mode_label = st.selectbox("Mode", list(_MODES))
        mode = _MODES[mode_label]
        is_mate = mode in _MATE_MODES
        source = _pills(  # filters already-imported games; does NOT fetch
            "Played on", common.SOURCES,
            help="Filters the drill to games you've **already imported** from "
            "this site — it doesn't fetch anything (importing lives on the "
            "Import page). Empty = every site.")
        if is_mate:
            cct_on = False
            missed_only = st.checkbox(
                "Only mates I missed (blown)",
                help="Drill the forced mates you failed to convert — the ones worth "
                "fixing — rather than every mate you had.",
            )
            count = st.selectbox("Puzzles", [20, 40], index=0)
            filt = dict(
                n=count, mode=mode, username=username, missed_only=missed_only,
                source=source,
            )
        else:
            style = st.segmented_control(
                "Drill style",
                ["Checks/Caps/Loose", "Best move"],
                default="Checks/Caps/Loose",
                help="**Checks/Caps/Loose** — first mark the checks, captures and "
                "loose pieces on the board, then play your move on the same "
                "board (drag or Shift-click). Trains the pre-move scan so you "
                "stop missing the obvious. Colour shows the kind; the piece you "
                "click first sets the side; click a piece twice to ring a loose "
                "piece.\n\n**Best move** — no scan: just find and play the best "
                "move.",
            )
            cct_on = style != "Best move"  # deselected -> the scan default
            # Difficulty up top: how hard the best move is to find (all drills),
            # and — on a scan drill — how busy the position is to scan.
            min_solve_depth = _DIFFICULTY[
                st.selectbox(
                    "Difficulty",
                    list(_DIFFICULTY),
                    help="How hard the best move is to find — the shallowest search "
                    "depth that already sees it. Higher levels drill finds that need "
                    "real calculation, past the quick recaptures. Most positions are "
                    "quick, so 'Any' is the everyday setting.",
                )
            ]
            scan_scope = "both"
            scan_load = None  # CCT scan-complexity band (min, max), or None = any
            if cct_on:
                scan_load = _SCAN_LOAD[st.selectbox(
                    "Scan complexity", list(_SCAN_LOAD), index=3,
                    help="How busy a position may be — the count of forcing moves "
                    "and loose pieces to scan (both sides). **Quiet** stays calm "
                    "(≤4 items); each step up allows busier positions; **Chaos** "
                    "is anything. Start low and work up. Applies to the next "
                    "drill you start.")]
                scope_label = st.segmented_control(
                    "Scan sides",
                    ["Both sides", "Mine", "Opponent's"],
                    default="Both sides",
                    help="Whose forcing moves you mark. **Both sides** is the "
                    "full workout. **Opponent's** is the pre-move blunder-check "
                    "— what can they do to me? **Mine** trains spotting your "
                    "own chances. Applies to the next drill you start.",
                )
                scan_scope = {"Mine": "me", "Opponent's": "opp"}.get(
                    scope_label or "Both sides", "both")
            tc = _pills("Time control", common.TC_CLASSES)
            st.caption("Pattern — pick any combination; empty = all:")
            structure = _pills("Structure", STRUCTURE_DEFS)
            move_type = _pills("Move type", MOVE_TYPE_DEFS)
            phase = _pills("Phase", PHASE_DEFS)
            opening_like = (
                st.text_input(
                    "Opening contains",
                    placeholder="e.g. french advance",
                    help="Drill one line — matches any opening whose name contains "
                    "these words. 'french' = all French; 'french advance' = the "
                    "Advance (all variants). Empty = all openings.",
                ).strip()
                or None
            )
            max_fullmove = None
            if opening_like:
                # An opening's character is in its first moves; deeper positions
                # have usually transformed past the structure/theory you drill.
                max_fullmove = int(
                    st.number_input(
                        "Opening depth — up to move #",
                        min_value=1,
                        max_value=40,
                        value=6,
                        help="Only the first N moves, where the opening's structure "
                        "and theory live. Deeper positions have transformed.",
                    )
                )
            count = st.selectbox("Puzzles", [20, 40], index=0)
            repeated = st.checkbox(
                "Only mistakes I've made before",
                help="Positions you blundered 2+ times across your games — the same "
                "mistake, made again.",
            )
            filt = dict(
                n=count,
                mode=mode,
                username=username,
                source=source,
                tc_class=tc,
                structure=structure,
                move_type=move_type,
                phase=phase,
                opening_like=opening_like,
                max_fullmove=max_fullmove,
                min_solve_depth=min_solve_depth,
                repeated_only=repeated,
                cct_scope=scan_scope,
                cct_load=scan_load,
            )
        # Render the Start button into the top slot (its code runs here, after
        # filt is built). It's green via the theme's primaryColor, like Next.
        with start_slot:
            if st.button("Start / restart drill", type="primary", width="stretch"):
                _new_queue(conn, **filt)

    state = st.session_state.get("trainer")
    if not state or not state["queue"]:
        st.info(
            "Configure a drill in the sidebar and press **Start**. "
            "If nothing loads, you have no graded mistakes for that filter yet."
        )
        return

    answered = state.get("answered", 0)
    total = state.get("total", 0)
    practice = state.get("practice", False)

    if practice:  # unscored retry pass (TRN-RETRY) — make it unmistakable
        st.info("🔁 **Practice** — reviewing your misses · not scored, nothing "
                "recorded. Press **New random drill** to return to a scored drill.")

    i, queue = state["i"], state["queue"]
    if i >= len(queue):
        label = "Practice" if practice else "Drill"
        if practice:
            st.success(f"{label} complete — you reviewed {len(queue)} position(s).")
        elif state.get("cct_max"):  # CCT drill: 1-each C/C/T + move score
            st.success(f"{label} complete — CCT score "
                       f"{state['cct_score']:g} / {state['cct_max']:g}.")
        else:
            st.success(
                f"{label} complete — {total:g} / {len(queue)} "
                f"(avg {total / answered:.2f})."
                if answered
                else f"{label} complete — {len(queue)} positions."
            )
        missed = _missed_positions(state)
        if missed and st.button(f"🔁 Retry my {len(missed)} "
                                f"miss{'es' if len(missed) != 1 else ''} — "
                                "not scored"):
            _start_practice(state, missed)
            st.rerun()
        if st.button("🔀 New random drill", type="primary"):
            _new_queue(conn, **filt)
            st.rerun()
        return

    pos = queue[i]
    board = chess.Board(pos["fen"])
    res = state["result"]
    mate = pos.get("mate")

    # Compact full-width header (position · colour · action · source), then two
    # columns below: the board on the left, the score boxes on the right.
    _over_board(conn, pos, board, i, len(queue), res is not None, cct_on)

    # Board left, scores right. (The board component sizes off the viewport —
    # min(90vmin, 600px), capped at 600 — not off its column, so a too-narrow
    # column clips it; keep the board column wide enough to show it whole.)
    left, right = st.columns([3, 2], gap="medium", vertical_alignment="top")
    with right:
        with st.container(border=True):
            st.markdown("**🏆 Challenge score**")
            _challenge_box(state, len(queue))
        puzzle_box = st.container(border=True)
        puzzle_box.markdown(
            "**Puzzle score**" if res is not None else "**This puzzle**")

    if res is not None:
        (_mate_review if mate else _review)(pos, board, state, res, left, puzzle_box)
    elif cct_on and not mate:
        _cct_beat(conn, pos, board, state, left, puzzle_box)  # scan + play, one board
    else:
        (_mate_puzzle if mate else _puzzle)(conn, pos, board, state, left, puzzle_box)
