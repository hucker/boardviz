"""Trainer page: drill your mistake positions with a timed +2..-2 score.

Each position: a short **bearings** pause (a couple of seconds to look, the
opponent's last move highlighted), then the **puzzle** — the clock starts and you
move. Think time is measured in the browser, so the pause isn't counted. In
**Auto** it flows hands-free (auto-start, auto-advance after the answer); with
Auto off you press Start for each and Next to move on. The score combines the
cached eval grade with the per-time-control penalty curve.
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
_DIFFICULTY = {"Any": None, "Skip obvious (≥6)": 6, "Hard (≥9)": 9, "Hardest (12)": 12}
_DIFF_WORD = {3: "obvious", 6: "medium", 9: "hard", 12: "very hard"}
_GRADE_WORD = {2: "Best", 1: "OK", 0: "Meh", -1: "Inaccuracy", -2: "Blunder"}
_BOARD_SIZE = 600  # match the interactive board so it doesn't resize between beats
_ADVANCE_RIGHT_MS = 500  # got it right in Auto mode: brief flash, then next
_ADVANCE_WRONG_MS = 2000  # missed it: a slower beat (Pause to study longer)
_BEARINGS_MS = 2000  # a beat to read the position before the clock starts


def _new_queue(conn, **filt) -> None:
    """Build a fresh drill queue from the sidebar filters (see render)."""
    if filt.get("mode") in _MATE_MODES:  # forced-mate drill, from mate_chances
        positions = trainer.select_mate_positions(
            conn, username=filt["username"], deep=filt["mode"] == "mate2",
            missed_only=filt.get("missed_only", False), n=filt.get("n", 20))
    else:
        positions = trainer.select_positions(conn, **filt)
    drill = st.session_state.get("_drill_n", 0) + 1  # unique per drill, for keys
    st.session_state["_drill_n"] = drill
    st.session_state.trainer = {
        "queue": positions,
        "i": 0,
        "result": None,
        "started": False,
        "review_move": None,
        "total": 0,
        "answered": 0,
        "drill": drill,
    }


def _bearings_for(pos: dict) -> dict:
    """A short 'get your bearings' pause before the clock, highlighting the
    opponent's last move (None if this was the game's first move)."""
    return {"delayMs": _BEARINGS_MS, "lastMove": pos.get("opp_move")}


def _advance(state: dict) -> None:
    """Move the drill to the next position (reset per-position state)."""
    state["i"] += 1
    state["result"] = None
    state["review_move"] = None
    state["started"] = False
    state["paused"] = False


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
    if "marked" in played:  # a CCT beat also returns the arrows/rings you drew
        scored["marked"] = played["marked"]
        (scored["cct_complete"], scored["cct_flawless"],
         scored["cct_found"], scored["cct_avail"]) = _accumulate_cct(
            state, board, played["marked"])
        pos_score = _cct_position_score(
            scored["cct_found"], scored["cct_avail"], scored["final_score"])
        scored["cct_pos_score"] = pos_score  # this position, out of 4
        state["cct_score"] = state.get("cct_score", 0) + pos_score
        state["cct_max"] = state.get("cct_max", 0) + 4
    state["result"] = scored
    state["total"] = state.get("total", 0) + scored["final_score"]
    state["answered"] = state.get("answered", 0) + 1
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


def _advance_controls(state: dict, got_it: bool, auto: bool) -> None:
    """Auto-advance (fast when right, slower when wrong, Pause to hold) or a
    manual Next button — shared by the puzzle and mate reviews."""
    if auto and not state.get("paused"):
        from streamlit_autorefresh import st_autorefresh

        delay = _ADVANCE_RIGHT_MS if got_it else _ADVANCE_WRONG_MS
        st.caption("Correct — next…" if got_it else "Next shortly — Pause to study")
        if st.button("⏸ Pause", key=f"pause-{state['i']}"):
            state["paused"] = True
            st.rerun()
        if st_autorefresh(interval=delay, key=f"auto-{state['drill']}-{state['i']}"):
            _advance(state)
            st.rerun()
    elif st.button("Next ▶", type="primary", key=f"next-{state['i']}"):
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
    trainer.record_attempt(
        conn, epd=pos["epd"], source="mate", played_uci=played["uci"],
        grade=2 if correct else -2, elapsed_s=elapsed, time_penalty=0,
        final_score=final, tc_class=pos["tc_class"])
    st.rerun()


def _mate_puzzle(conn, pos: dict, board: chess.Board, state: dict,
                 left, right) -> None:
    """The mate drill: you had a forced mate here — find the move."""
    turn = "White" if board.turn else "Black"
    d = pos["distance"]
    task = "deliver checkmate." if d == 1 else f"play the move that forces mate in {d}."
    with left:
        played = boardui.board_input(
            board, key=f"mate-board-{state['i']}", intro=_bearings_for(pos))
        st.caption(f"{turn} to move — {task}")
    with right:
        st.caption("You had a forced mate here — find the move. No hints.")
    if played:
        _commit_mate(conn, pos, board, state, played)


def _mate_review(pos: dict, board: chess.Board, state: dict, res: dict,
                 left, right, *, auto: bool) -> None:
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
        _score_line(res["final_score"])
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
        st.caption(f"took {res['elapsed']:.1f}s (not scored)")
        if pos.get("url"):
            st.markdown(f"[Open game]({pos['url']})")
        _advance_controls(state, res["correct"], auto)


def _cct_legend() -> None:
    """A popover explaining the CCT board's layers, colours and gestures."""
    with st.popover("🎨 Colour key", help="What the marks on the board mean"):
        st.markdown(
            "**Work one layer at a time** with the board tabs — "
            ":blue-badge[Checks] :orange-badge[Captures] "
            ":red-badge[Threats] — for you *and* your opponent."
        )
        st.markdown(
            "**Checks / Captures**  \nclick a piece, then its target square (an arrow)."
        )
        st.markdown(
            "**Threats** (a piece that can be won)  \nclick the loose "
            "piece — a ring appears."
        )
        st.markdown("**Side — the line**  \nsolid = you · dashed = the opponent")
        st.markdown("**Each mark is graded**  \n✓ correct · ✗ not that kind")
        st.markdown(
            "**Play your move** by dragging a piece (or Shift-click its target)."
        )


def _cct_beat(conn, pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """Both-ways CCT: scan checks/captures/threats for each side, then play your
    move on the same board (drag or Shift-click)."""
    scan = cct.scan_both(board)
    with left:
        played = boardui.board_scan(
            board, scan, key=f"cct-{state['i']}", last_move=pos.get("opp_move")
        )
        st.info(
            "▶ **To finish: play your move** — **drag** a piece, or hold "
            "**Shift** and click its target square. That answers the puzzle "
            "and ends the scan (there's no separate Done button)."
        )
    with right:
        _cct_legend()
        st.caption(
            "**Scan first (both ways).** Use the **Checks / Captures / "
            "Threats** tabs on the board to mark one layer at a time — for "
            "**you** *and* your **opponent**. Each mark is graded ✓/✗; your "
            "tally and what you missed show after you move."
        )
    if played:
        _commit_move(conn, pos, board, state, played)


def _side_line(board: chess.Board) -> str:
    """A bold 'which colour am I' label — the board orientation alone can be
    ambiguous, especially in sparse endgames (TRN-INTRO)."""
    chip = "⚪" if board.turn else "⚫"
    return f"{chip} You're playing **{'White' if board.turn else 'Black'}**"


def _score_line(final: float) -> None:
    color = "🟢" if final >= 1 else "🟨" if final >= 0.5 else "🟥"
    label = f"+{final:g}" if final > 0 else "0"
    st.markdown(f"### {color}  Score: {label}")


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


def _accumulate_cct(state: dict, board: chess.Board, marked: dict) -> tuple:
    """Add a CCT position's result to the drill's running tally.

    Updates ``state['cct_found']`` / ``state['cct_avail']`` (the drill totals) and
    returns ``(complete, flawless, found, avail)`` for *this* position — where
    ``found``/``avail`` are {side: {cat: n}} dicts for the per-puzzle scoreboard.
    *complete* = you found every check/capture/threat available; *flawless* =
    complete **and** you made no wrong marks either.
    """
    scan = cct.scan_both(board)
    found = _cct_counts(board, marked, scan)
    avail = {s: {c: len(scan[s][c]) for c in _CCT_CATS} for s in ("me", "opp")}
    run_f = state.setdefault("cct_found", _zero_cct())
    run_a = state.setdefault("cct_avail", _zero_cct())
    avail_pos = found_pos = 0
    for s in ("me", "opp"):
        for c in _CCT_CATS:
            run_f[s][c] += found[s][c]
            run_a[s][c] += avail[s][c]
            avail_pos += avail[s][c]
            found_pos += found[s][c]
    marked_pos = sum(len(marked.get(c, [])) for c in _CCT_CATS)
    complete = avail_pos > 0 and found_pos == avail_pos
    flawless = complete and marked_pos == found_pos
    return complete, flawless, found, avail


def _cct_position_score(found: dict, avail: dict, move_score: float) -> float:
    """A CCT position's score, out of 4: one point for each category fully found
    (checks / captures / threats, both sides) plus the move score (0 / 0.5 / 1)."""
    cats = sum(
        1 for c in _CCT_CATS
        if found["me"][c] + found["opp"][c] == avail["me"][c] + avail["opp"][c])
    return cats + move_score


_CAT_COLOR = {"checks": "#3b82f6", "captures": "#f59e0b", "threats": "#ef4444"}


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
                 f'fill="{_CAT_COLOR[c]}">{c}</text>')
    for i, (side, label) in enumerate((("me", "You"), ("opp", "Opp"))):
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


def _start_gate(pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """Manual mode: show the position with a Start button before the clock."""
    with left:
        boardui.show_board(board, size=_BOARD_SIZE, orientation=board.turn)
        st.caption("Your move to find — press Start when you're ready.")
    with right:
        if st.button("▶ Start", type="primary", key=f"start-{state['i']}"):
            state["started"] = True
            st.rerun()


def _puzzle(conn, pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """The live, interactive puzzle: play your move; the clock is client-side."""
    turn = "White" if board.turn else "Black"
    with left:
        played = boardui.board_input(
            board, key=f"trainer-board-{state['i']}", intro=_bearings_for(pos)
        )
        st.caption(f"{turn} to move — make your move on the board.")
    with right:
        st.caption("Play the move you think is best — no hints.")
    if played:
        _commit_move(conn, pos, board, state, played)


def _review(
    pos: dict, board: chess.Board, state: dict, res: dict, left, right, *, auto: bool
) -> None:
    """Answered: your move (always red) + a highlighted alternative to compare."""
    grades = pos["grades"]
    best, played = pos["best_uci"], res["uci"]
    plus = sorted(
        ((u, g) for u, g in grades.items() if g >= 1), key=lambda ug: (-ug[1], ug[0])
    )
    sel = state.get("review_move") or best

    def _color(uci: str) -> str:  # good move green, mistake red
        return "#2c7" if grades.get(uci, -2) >= 1 else "#c33"

    arrows = []
    if sel != played:  # the alternative you're inspecting
        sm = chess.Move.from_uci(sel)
        arrows.append(chess.svg.Arrow(sm.from_square, sm.to_square, color=_color(sel)))
    pm = chess.Move.from_uci(played)  # your move, drawn on top
    arrows.append(chess.svg.Arrow(pm.from_square, pm.to_square, color=_color(played)))

    marked = res.get("marked")
    cct_scan = cct.scan_both(board) if marked is not None else None
    with left:
        if cct_scan is not None:  # CCT beat: reveal the both-ways scan, by layer
            boardui.board_scan(
                board,
                cct_scan,
                key=f"cct-rev-{state['i']}",
                reveal=True,
                played=played,
                marked=marked,
            )
            _cct_legend()
            st.caption(
                "Flip layers with the board tabs — **bright = you missed "
                "it**, faded = found · solid = you, dashed = opponent."
            )
        else:
            boardui.show_board(
                board, size=_BOARD_SIZE, arrows=arrows, orientation=board.turn
            )
            st.caption("Green = a good move, red = a mistake — your move is on top.")
    with right:
        _score_line(res["final_score"])
        if cct_scan is not None:
            got_move = res["grade"] >= 1  # a good/best move — required for the effects
            if not res.get("celebrated"):  # once per position, on entering review
                res["celebrated"] = True
                if got_move and res.get("cct_flawless"):
                    st.snow()
                elif got_move and res.get("cct_complete"):
                    st.balloons()
            found = _cct_counts(board, marked or {}, cct_scan)
            avail_pos = sum(len(cct_scan[s][c]) for s in ("me", "opp") for c in _CCT_CATS)
            missed = avail_pos - sum(
                found[s][c] for s in ("me", "opp") for c in _CCT_CATS)
            if res.get("cct_complete"):
                tag = "Flawless" if res.get("cct_flawless") else "Clean"
                if got_move:
                    st.success(f"✨ {tag} scan — and the right move. Nailed it!")
                else:
                    st.info(f"{tag} scan — now find the best move for the celebration.")
            else:
                st.warning(f"Missed {missed} of {avail_pos} this position — "
                           "shown bright on the board.")
            if res.get("cct_found"):  # this puzzle's own breakdown, beside the score
                ps = res.get("cct_pos_score", 0)
                _cct_scoreboard(res["cct_found"], res["cct_avail"],
                                title="This position", note=f"{ps:g}/4",
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
        st.caption(f"took {res['elapsed']:.1f}s (not scored)")
        st.write(grading.win_loss_readout(pos["eval_cp"]))

        st.caption("Compare on the board — click a move:")
        # The best move stands on its own, above the alternatives.
        g_best = grades.get(best, 2)
        best_tag = "  ← you" if played == best else ""
        if st.button(
            f"{'▶ ' if sel == best else ''}⭐ Best — {best_san} "
            f"({g_best:+d}){best_tag}",
            type="primary",
            width="stretch",
            key=f"opt-{state['i']}-{best}",
        ):
            state["review_move"] = best
            st.rerun()
        # Then any other good moves, plus your move if it wasn't one of them.
        shown = {u for u, _ in plus}
        others = [(u, g) for u, g in plus if u != best]
        if played != best and played not in shown:
            others.append((played, grades.get(played, 0)))
        if others:
            st.caption("Other options:")
        for u, g in others:
            word = _GRADE_WORD.get(g, f"{g:+d}")
            tag = "  ← you" if u == played else ""
            mark = "▶ " if u == sel else ""
            san = board.san(chess.Move.from_uci(u))
            if st.button(
                f"{mark}{word} {g:+d} — {san}{tag}",
                key=f"opt-{state['i']}-{u}",
                width="stretch",
            ):
                state["review_move"] = u
                st.rerun()

        # Auto keeps cycling — fast when right, a slower beat when wrong — but a
        # Pause stops the timer so you can study a miss for as long as you like.
        _advance_controls(state, res["grade"] >= 1, auto)


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

    def _pills(label, values):  # multi-select; [] (empty) means all
        return st.pills(label, list(values), selection_mode="multi") or None

    with st.sidebar:
        st.subheader("Drill setup")
        mode_label = st.selectbox("Mode", list(_MODES))
        mode = _MODES[mode_label]
        is_mate = mode in _MATE_MODES
        auto = st.checkbox(
            "Auto (hands-free)",
            value=True,
            help="On: each puzzle auto-starts after a ~2s look and auto-advances "
            "once you answer. Off: press Start for each, Next to move on.",
        )
        if is_mate:
            cct_on = False
            missed_only = st.checkbox(
                "Only mates I missed (blown)",
                help="Drill the forced mates you failed to convert — the ones worth "
                "fixing — rather than every mate you had.",
            )
            count = st.selectbox("Puzzles", [20, 40], index=0)
            filt = dict(
                n=count, mode=mode, username=username, missed_only=missed_only
            )
        else:
            cct_on = st.checkbox(
                "Scan first (CCT)",
                help="Mark the checks, captures and threats on the board — for both "
                "you and your opponent — then play your move on the same board "
                "(drag or Shift-click). Trains the pre-move scan so you stop "
                "missing the obvious. Colour shows the kind; the piece you click "
                "first sets the side; click a piece twice to ring a threat.",
            )
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
            min_solve_depth = _DIFFICULTY[
                st.selectbox(
                    "Difficulty",
                    list(_DIFFICULTY),
                    help="How hard the best move is to find — the shallowest search "
                    "depth that already sees it. 'Skip obvious' drops the recaptures "
                    "you'd get anyway and drills finds that need real calculation.",
                )
            ]
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
                tc_class=tc,
                structure=structure,
                move_type=move_type,
                phase=phase,
                opening_like=opening_like,
                max_fullmove=max_fullmove,
                min_solve_depth=min_solve_depth,
                repeated_only=repeated,
            )
        if st.button("Start / restart drill", type="primary"):
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
    if state.get("cct_avail"):  # CCT drill: score (1 each C/C/T + move) in the graphic
        cs, cm = state.get("cct_score", 0), state.get("cct_max", 0)
        perfect = cm > 0 and cs == cm  # every category found and every move right
        _cct_scoreboard(state["cct_found"], state["cct_avail"],
                        title="CCT — drill total", note=f"{cs:g}/{cm:g}",
                        note_color="#16a34a" if perfect else "#6b7280", scale=1.3)
    elif answered:  # plain puzzle drill keeps the native running-score metric
        st.metric(
            "Running score", f"{total:g} / {answered}", f"avg {total / answered:.2f}"
        )

    i, queue = state["i"], state["queue"]
    if i >= len(queue):
        if state.get("cct_max"):  # CCT drill: report the 1-each C/C/T + move score
            st.success(f"Drill complete — CCT score "
                       f"{state['cct_score']:g} / {state['cct_max']:g}.")
        else:
            st.success(
                f"Drill complete — {total:g} / {len(queue)} (avg {total / answered:.2f})."
                if answered
                else f"Drill complete — {len(queue)} positions."
            )
        if st.button("🔀 New random drill", type="primary"):
            _new_queue(conn, **filt)
            st.rerun()
        return

    pos = queue[i]
    board = chess.Board(pos["fen"])
    if pos.get("mate"):
        st.caption(f"Position {i + 1} / {len(queue)} — **Mate in {pos['distance']}**"
                   + (f" · {pos['motif']}" if pos.get("motif") else ""))
    else:
        diff = _DIFF_WORD.get(pos.get("solve_depth"))
        st.caption(
            f"Position {i + 1} / {len(queue)} — "
            f"{pos['structure']} · {pos['move_type']} · {pos['phase']} · "
            f"{pos['tc_class']}" + (f" · {diff} find" if diff else "")
        )

    res = state["result"]
    left, right = st.columns([3, 2])
    if res is not None:
        (_mate_review if pos.get("mate") else _review)(
            pos, board, state, res, left, right, auto=auto)
    elif cct_on and not pos.get("mate"):
        _cct_beat(conn, pos, board, state, left, right)  # scan + play, one board
    elif not auto and not state.get("started"):
        _start_gate(pos, board, state, left, right)  # manual: wait for Start
    else:
        (_mate_puzzle if pos.get("mate") else _puzzle)(
            conn, pos, board, state, left, right)

    st.markdown(f"### {_side_line(board)}")  # which colour you are — kept at the foot
