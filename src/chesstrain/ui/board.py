"""Board rendering (python-chess SVG) and legal-move input helpers.

The static board SVG is shown via ``st.iframe`` with a ``data:`` URI — st.image
can't render SVG, and st.html strips it (Streamlit's DOM sanitizer drops <svg>),
so it goes in an iframe like the old components.v1.html did. The interactive
move-entry board is a Custom Components v2 element.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable

import chess
import chess.svg
import streamlit as st
import streamlit.components.v2 as components_v2


# Match the interactive board component's squares (see _BOARD_INPUT_CSS) so the
# static SVG board and the click-to-move board share one green/cream palette.
_SVG_COLORS = {"square light": "#ebecd0", "square dark": "#779556"}


def board_svg(board: chess.Board, *, size: int = 380,
              lastmove: chess.Move | None = None, arrows: Iterable = (),
              fill: dict | None = None, orientation: bool | None = None) -> str:
    """Render `board` to an SVG string, oriented to the side to move by default.

    ``fill`` tints squares ({square_index: css_color}) — used to highlight the
    piece about to move in the trainer's pre-puzzle preview.
    """
    return chess.svg.board(
        board, size=size, lastmove=lastmove, arrows=list(arrows),
        fill=fill or {}, colors=_SVG_COLORS,
        orientation=board.turn if orientation is None else orientation,
    )


def show_board(board: chess.Board, *, size: int = 380,
               lastmove: chess.Move | None = None, arrows: Iterable = (),
               fill: dict | None = None, orientation: bool | None = None) -> None:
    """Render a board into the current Streamlit container."""
    svg = board_svg(board, size=size, lastmove=lastmove, arrows=arrows,
                    fill=fill, orientation=orientation)
    html = f'<body style="margin:0"><div style="display:flex">{svg}</div></body>'
    data = "data:text/html;base64," + base64.b64encode(html.encode()).decode()
    st.iframe(data, height=size + 12)


def legal_move_labels(board: chess.Board) -> dict[str, str]:
    """Map SAN -> UCI for every legal move, sorted for a stable dropdown."""
    return dict(sorted((board.san(m), m.uci()) for m in board.legal_moves))


# --- interactive move-entry board (Custom Components v2) --------------------
# A click-and-drag chessboard for the trainer. The legal-move list is handed to
# the frontend ONLY to validate/bounce illegal moves — it is never rendered, so
# the board never reveals which pieces can move. That's the whole point: the
# drill is spoiled the moment the UI enumerates candidate moves for you.
_BOARD_INPUT_CSS = """
.ct-board {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  grid-template-rows: repeat(8, 1fr);
  width: min(90vmin, 600px);
  aspect-ratio: 1 / 1;
  border: 2px solid var(--st-secondary-background-color, #3a3a3a);
  border-radius: 4px;
  overflow: hidden;
  user-select: none;
  touch-action: manipulation;
}
.ct-sq {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}
.ct-sq.light { background: #ebecd0; }
.ct-sq.dark  { background: #779556; }
.ct-piece {
  font-size: min(11vmin, 74px);
  line-height: 1;
  z-index: 1;
}
.ct-piece.own { cursor: grab; }
.ct-piece.w { color: #fafafa; text-shadow: 0 0 2px #000, 0 1px 2px #000; }
.ct-piece.b { color: #1c1c1c; text-shadow: 0 0 2px #d8d8d8; }
.ct-sq.sel { outline: 3px solid var(--st-primary-color, #4c9be8); outline-offset: -3px; }
.ct-sq.moved { box-shadow: inset 0 0 0 5px rgba(250, 204, 21, .6); }
.ct-sq.hint::after {
  content: ""; position: absolute; width: 28%; height: 28%;
  border-radius: 50%; background: rgba(20,20,20,.28); pointer-events: none;
}
.ct-sq.cap::after {
  content: ""; position: absolute; inset: 7%;
  border-radius: 50%; border: 5px solid rgba(20,20,20,.28); pointer-events: none;
}
"""

_BOARD_INPUT_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const fen = (data && data.fen) || "8/8/8/8/8/8/8/8 w - - 0 1";
  const orientation = (data && data.orientation) || "white";
  const legal = new Set((data && data.legal) || []);
  const intro = (data && data.intro) || null;  // {delayMs, lastMove} bearings pause

  // Filled glyphs for both colors (crisper than outline glyphs on a board);
  // color is carried by CSS, not the codepoint.
  const GLYPH = {k:0x265A, q:0x265B, r:0x265C, b:0x265D, n:0x265E, p:0x265F};
  const files = "abcdefgh".split("");
  const rankOrder = orientation === "white" ? [8,7,6,5,4,3,2,1] : [1,2,3,4,5,6,7,8];
  const fileOrder = orientation === "white" ? files : files.slice().reverse();

  // Short synthesized blips (no audio assets). Best-effort — a browser may mute
  // until a gesture, so the user's own move (a click) is always audible.
  const tone = (freq, durMs, type) => {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      const ctx = parentElement.__ctAudio || (parentElement.__ctAudio = new AC());
      if (ctx.state === "suspended") ctx.resume();
      const t = ctx.currentTime;
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = type || "triangle"; o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.28, t + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t + durMs / 1000);
      o.connect(g); g.connect(ctx.destination);
      o.start(t); o.stop(t + durMs / 1000 + 0.02);
    } catch (e) { /* audio not available */ }
  };
  const startSound = () => tone(620, 130, "sine");
  const moveSound = () => tone(320, 80, "triangle");

  const parseFen = (f) => {
    const parts = f.split(" ");
    const pieces = {};
    const ranks = parts[0].split("/");           // rank 8 first
    for (let r = 0; r < 8; r++) {
      let file = 0;
      for (const ch of ranks[r] || "") {
        if (ch >= "1" && ch <= "8") { file += parseInt(ch, 10); }
        else { pieces["abcdefgh"[file] + (8 - r)] = ch; file += 1; }
      }
    }
    return { pieces, sideToMove: parts[1] || "w" };
  };

  // Build a board element for a position; returns { boardEl, cells }.
  const renderBoard = (f) => {
    const { pieces } = parseFen(f);
    const boardEl = document.createElement("div");
    boardEl.className = "ct-board";
    Object.assign(boardEl.style, {
      display: "grid", gridTemplateColumns: "repeat(8, 1fr)",
      gridTemplateRows: "repeat(8, 1fr)", width: "min(90vmin, 600px)",
      aspectRatio: "1 / 1",
    });
    const cells = {};
    for (const rank of rankOrder) {
      for (const f2 of fileOrder) {
        const sq = f2 + rank;
        const light = ("abcdefgh".indexOf(f2) + rank) % 2 === 0;
        const cell = document.createElement("div");
        cell.className = "ct-sq " + (light ? "light" : "dark");
        Object.assign(cell.style, {
          position: "relative", display: "flex", alignItems: "center",
          justifyContent: "center", background: light ? "#ebecd0" : "#779556",
        });
        const p = pieces[sq];
        if (p) {
          const white = p === p.toUpperCase();
          const span = document.createElement("span");
          span.className = "ct-piece " + (white ? "w" : "b");
          Object.assign(span.style, {
            fontSize: "min(11vmin, 74px)", lineHeight: "1",
            color: white ? "#fafafa" : "#1c1c1c",
            textShadow: white ? "0 0 2px #000, 0 1px 2px #000" : "0 0 2px #d8d8d8",
          });
          span.textContent = String.fromCodePoint(GLYPH[p.toLowerCase()]);
          cell.appendChild(span);
        }
        boardEl.appendChild(cell);
        cells[sq] = cell;
      }
    }
    return { boardEl, cells };
  };

  // --- puzzle (interactive) state --------------------------------------------
  const puzzle = parseFen(fen);
  const isOwn = (p) => !!p && (puzzle.sideToMove === "w"
    ? p === p.toUpperCase() : p === p.toLowerCase());
  const resolve = (from, to) => {
    if (legal.has(from + to)) return from + to;
    if (legal.has(from + to + "q")) return from + to + "q";  // auto-queen
    return null;
  };
  const targetsOf = (from) => {
    const t = new Set();
    legal.forEach(u => { if (u.slice(0, 2) === from) t.add(u.slice(2, 4)); });
    return t;
  };

  let cells = {};
  let selected = null;
  let submitted = false;
  let startTime = 0;

  const clearHints = () => Object.values(cells).forEach(
    c => c.classList.remove("sel", "hint", "cap"));
  const select = (sq) => {
    selected = sq;
    clearHints();
    cells[sq].classList.add("sel");
    targetsOf(sq).forEach(to => {
      if (cells[to]) cells[to].classList.add(puzzle.pieces[to] ? "cap" : "hint");
    });
  };
  const tryMove = (from, to) => {
    if (submitted) return false;
    const uci = resolve(from, to);
    if (!uci) return false;
    submitted = true;
    clearHints();
    moveSound();
    const ms = Math.max(0, Math.round(performance.now() - startTime));
    setTriggerValue("move", { uci: uci, ms: ms });  // client-measured think time
    return true;
  };

  // Attach input once the puzzle position is live and start the clock.
  const enableInput = () => {
    for (const sq in cells) {
      const cell = cells[sq];
      const p = puzzle.pieces[sq];
      if (p && isOwn(p)) {
        const span = cell.querySelector(".ct-piece");
        if (span) {
          span.style.cursor = "grab";
          span.draggable = true;
          span.addEventListener("dragstart", (e) => {
            select(sq); e.dataTransfer.setData("text/plain", sq);
          });
        }
      }
      cell.addEventListener("click", () => {
        if (submitted) return;
        if (selected && selected !== sq && tryMove(selected, sq)) return;
        if (isOwn(puzzle.pieces[sq])) select(sq);
        else { clearHints(); selected = null; }
      });
      cell.addEventListener("dragover", (e) => e.preventDefault());
      cell.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = e.dataTransfer.getData("text/plain");
        if (from) tryMove(from, sq);
      });
    }
    startTime = performance.now();
  };

  const showPuzzle = (highlight, delayMs) => {
    const cur = parentElement.querySelector(".ct-board");
    if (cur) cur.remove();
    const r = renderBoard(fen);
    parentElement.appendChild(r.boardEl);
    cells = r.cells;
    if (highlight) {  // the opponent's last move — kept lit for the whole puzzle
      const from = highlight.slice(0, 2), to = highlight.slice(2, 4);
      [from, to].forEach(s => { if (cells[s]) cells[s].classList.add("moved"); });
    }
    if (delayMs && delayMs > 0) {  // "bearings": a beat to look before the clock
      prog.style.background = "rgba(128,128,128,.25)";
      requestAnimationFrame(() => {
        fill.style.transition = "width " + delayMs + "ms linear";
        fill.style.width = "100%";
      });
      parentElement.__ctTimer = setTimeout(() => {
        parentElement.__ctTimer = null;
        if (submitted) return;
        prog.style.background = "transparent";
        fill.style.width = "0%";
        enableInput();  // clock starts now
      }, delayMs);
    } else {
      enableInput();
    }
  };

  parentElement.innerHTML = "";
  // Inject the stylesheet into the component's own DOM — the component css= arg
  // never reached it (squares stacked in a column). The grid/colors are also
  // hard-set inline in renderBoard, so layout holds regardless.
  const styleEl = document.createElement("style");
  styleEl.textContent = %CT_CSS%;
  parentElement.appendChild(styleEl);

  // Cancel any intro timer left over from a prior run on this element.
  if (parentElement.__ctTimer) clearTimeout(parentElement.__ctTimer);

  // A fixed-height header row that always reserves space, so the board never
  // jumps when the intro progress bar finishes. It's transparent unless a replay
  // is actively running.
  const prog = document.createElement("div");
  Object.assign(prog.style, {
    width: "min(90vmin, 600px)", height: "8px", marginBottom: "8px",
    background: "transparent", borderRadius: "4px", overflow: "hidden",
  });
  const fill = document.createElement("div");
  Object.assign(fill.style, {
    width: "0%", height: "100%", background: "rgba(90, 90, 90, .55)",
  });
  prog.appendChild(fill);
  parentElement.appendChild(prog);

  // Run the "bearings" countdown at most once per position: a stray rerun (e.g.
  // touching a sidebar widget) must not restart the countdown, re-trigger sound,
  // or reset the clock.
  const introDone = parentElement.__ctIntroKey === fen;
  const last = (intro && intro.lastMove) || null;   // opponent's move into here
  const delay = (intro && intro.delayMs) ? intro.delayMs : 0;

  if (!introDone) {
    parentElement.__ctIntroKey = fen;
    if (delay > 0) startSound();
    showPuzzle(last, delay);
  } else {
    showPuzzle(last, 0);  // already oriented; hand over immediately
  }

  return () => { if (parentElement.__ctTimer) clearTimeout(parentElement.__ctTimer); };
}
"""

# The component css= arg didn't reach the board's DOM, so inject the stylesheet
# from JS instead (json.dumps makes it a safe JS string literal).
_BOARD_INPUT_JS = _BOARD_INPUT_JS.replace("%CT_CSS%", json.dumps(_BOARD_INPUT_CSS))

_BOARD_INPUT = components_v2.component(
    "chesstrain_board_input",
    js=_BOARD_INPUT_JS,
)


def board_input(board: chess.Board, *, key: str,
                intro: dict | None = None) -> dict | None:
    """Interactive move-entry board; returns {"uci", "ms"} once the user moves.

    Click a piece then a square, or drag it — both work. Promotions auto-queen.
    The legal-move list is sent to the frontend only to reject illegal moves; it
    is never shown, so nothing tells you which piece to move. ``ms`` is the think
    time measured in the browser, so it starts only after the bearings pause.

    ``intro`` (optional) is a short "get your bearings" pause before the clock:
    ``{"delayMs": int, "lastMove": uci | None}`` — the board shows for ``delayMs``
    (a progress bar counts it down, the opponent's ``lastMove`` highlighted), then
    the clock starts and it's your move.
    """
    result = _BOARD_INPUT(
        key=key,
        data={
            "fen": board.fen(),
            "orientation": "white" if board.turn else "black",
            "legal": [m.uci() for m in board.legal_moves],
            "intro": intro,
        },
        on_move_change=lambda: None,
    )
    return getattr(result, "move", None)


# --- CCT marking board (Custom Components v2) -------------------------------
# A static board you draw arrows on: click a piece then a target and it marks
# that move, coloured live by whether it is a check / capture / both / neither.
# "Reveal" then dashes in any check or capture you missed. Nothing is submitted —
# it's a pure scanning drill (the trainer proceeds via a separate Streamlit
# button). All styling is inline so there is no external stylesheet to go astray.
_BOARD_MARK_JS = r"""
export default function (component) {
  const { data, parentElement } = component;
  const fen = (data && data.fen) || "8/8/8/8/8/8/8/8 w - - 0 1";
  const orientation = (data && data.orientation) || "white";
  const checks = new Set((data && data.checks) || []);
  const captures = new Set((data && data.captures) || []);

  const COLOR = { check: "#3b82f6", capture: "#f59e0b",
                  both: "#8b5cf6", none: "#9ca3af" };
  const GLYPH = { k:0x265A, q:0x265B, r:0x265C, b:0x265D, n:0x265E, p:0x265F };
  const files = "abcdefgh".split("");
  const white = orientation === "white";
  const rankOrder = white ? [8,7,6,5,4,3,2,1] : [1,2,3,4,5,6,7,8];
  const fileOrder = white ? files : files.slice().reverse();

  const parseFen = (f) => {
    const ranks = f.split(" ")[0].split("/");     // rank 8 first
    const pieces = {};
    for (let r = 0; r < 8; r++) {
      let file = 0;
      for (const ch of ranks[r] || "") {
        if (ch >= "1" && ch <= "8") file += parseInt(ch, 10);
        else { pieces["abcdefgh"[file] + (8 - r)] = ch; file += 1; }
      }
    }
    return pieces;
  };

  // Square -> [col, row] in the DISPLAY grid (0,0 = top-left), honouring flip.
  const colRow = (sq) => {
    const f = sq.charCodeAt(0) - 97, r = parseInt(sq[1], 10);
    return [white ? f : 7 - f, white ? 8 - r : r - 1];
  };
  const center = (sq) => { const [c, rw] = colRow(sq); return [c + 0.5, rw + 0.5]; };

  const kind = (uci) => {
    const chk = checks.has(uci) || checks.has(uci + "q");   // auto-queen match
    const cap = captures.has(uci) || captures.has(uci + "q");
    return chk && cap ? "both" : chk ? "check" : cap ? "capture" : "none";
  };

  // --- build the board grid ---
  const pieces = parseFen(fen);
  const wrap = document.createElement("div");
  Object.assign(wrap.style, { position: "relative", width: "min(90vmin, 600px)",
    aspectRatio: "1 / 1" });
  const grid = document.createElement("div");
  Object.assign(grid.style, { position: "absolute", inset: "0",
    display: "grid", gridTemplateColumns: "repeat(8, 1fr)",
    gridTemplateRows: "repeat(8, 1fr)", border: "2px solid #3a3a3a",
    borderRadius: "4px", overflow: "hidden", userSelect: "none" });
  const cells = {};
  for (const rank of rankOrder) {
    for (const f of fileOrder) {
      const sq = f + rank;
      const light = ("abcdefgh".indexOf(f) + rank) % 2 === 0;
      const cell = document.createElement("div");
      Object.assign(cell.style, { position: "relative", display: "flex",
        alignItems: "center", justifyContent: "center", cursor: "pointer",
        background: light ? "#ebecd0" : "#779556" });
      const p = pieces[sq];
      if (p) {
        const span = document.createElement("span");
        const isW = p === p.toUpperCase();
        Object.assign(span.style, { fontSize: "min(11vmin, 74px)", lineHeight: "1",
          color: isW ? "#fafafa" : "#1c1c1c", pointerEvents: "none",
          textShadow: isW ? "0 0 2px #000, 0 1px 2px #000" : "0 0 2px #d8d8d8" });
        span.textContent = String.fromCodePoint(GLYPH[p.toLowerCase()]);
        cell.appendChild(span);
      }
      cell.addEventListener("click", () => onClick(sq));
      grid.appendChild(cell);
      cells[sq] = cell;
    }
  }
  wrap.appendChild(grid);

  // --- arrow overlay (SVG, board coords 0..8), clicks pass through ---
  const SVGNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 8 8");
  Object.assign(svg.style, { position: "absolute", inset: "0", width: "100%",
    height: "100%", pointerEvents: "none" });
  wrap.appendChild(svg);

  const el = (name, attrs) => {
    const n = document.createElementNS(SVGNS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };
  const drawArrow = (from, to, color, dashed) => {
    const [x1, y1] = center(from), [x2, y2] = center(to);
    const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;         // unit direction
    const head = 0.42, halfw = 0.17;            // arrowhead size (square units)
    const bx = x2 - ux * head, by = y2 - uy * head;   // where the head begins
    const px = -uy, py = ux;                     // perpendicular
    const op = dashed ? 0.55 : 0.95;
    const line = el("line", { x1, y1, x2: bx, y2: by, stroke: color,
      "stroke-width": 0.16, "stroke-linecap": "round", opacity: op });
    if (dashed) line.setAttribute("stroke-dasharray", "0.28 0.2");
    svg.appendChild(line);
    svg.appendChild(el("polygon", { points:
      `${x2},${y2} ${bx + px * halfw},${by + py * halfw} `
      + `${bx - px * halfw},${by - py * halfw}`, fill: color, opacity: op }));
  };

  // --- marking state ---
  let sel = null;                     // first square clicked
  const marked = new Map();           // uci -> {from, to, t}
  let revealed = false;

  const select = (sq, on) => {
    cells[sq].style.boxShadow = on ? "inset 0 0 0 4px #facc15" : "none";
  };
  const redraw = () => {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    for (const m of marked.values()) drawArrow(m.from, m.to, COLOR[m.t], false);
    if (revealed) {
      for (const uci of new Set([...checks, ...captures])) {
        if (marked.has(uci)) continue;               // only what you MISSED
        drawArrow(uci.slice(0, 2), uci.slice(2, 4), COLOR[kind(uci)], true);
      }
    }
  };
  const onClick = (sq) => {
    if (revealed) return;
    if (sel === null) { sel = sq; select(sq, true); return; }
    select(sel, false);
    if (sel === sq) { sel = null; return; }          // clicked twice -> cancel
    const from = sel, uci = sel + sq; sel = null;
    if (marked.has(uci)) marked.delete(uci);         // toggle off
    else marked.set(uci, { from, to: sq, t: kind(uci) });
    redraw();
  };

  // --- reveal button ---
  const bar = document.createElement("div");
  Object.assign(bar.style, { marginTop: "8px", display: "flex", gap: "8px",
    alignItems: "center", width: "min(90vmin, 600px)" });
  const btn = document.createElement("button");
  btn.textContent = "Reveal what I missed";
  Object.assign(btn.style, { padding: "4px 12px", borderRadius: "6px",
    border: "1px solid #888", background: "#f3f4f6", cursor: "pointer" });
  const note = document.createElement("span");
  Object.assign(note.style, { fontSize: "0.85em", color: "#6b7280" });
  note.textContent = "Blue = check · Orange = capture · Purple = both · Grey = neither";
  btn.onclick = () => {
    revealed = true; btn.disabled = true; btn.style.opacity = "0.5";
    note.textContent = `Missed shown dashed — ${checks.size} checks, `
      + `${captures.size} captures in this position.`;
    if (sel) { select(sel, false); sel = null; }
    redraw();
  };
  bar.appendChild(btn); bar.appendChild(note);

  parentElement.innerHTML = "";
  parentElement.appendChild(wrap);
  parentElement.appendChild(bar);
}
"""

_BOARD_MARK = components_v2.component("chesstrain_board_mark", js=_BOARD_MARK_JS)


def board_mark(board: chess.Board, checks: list[str], captures: list[str], *,
               key: str) -> None:
    """A static board to mark checks/captures on (the CCT scan drill).

    Click a piece then a target to draw a colour-coded arrow (blue check, orange
    capture, purple both, grey neither); "Reveal" dashes in any you missed. Pure
    practice — nothing is returned; the trainer advances via its own button.
    """
    _BOARD_MARK(
        key=key,
        data={
            "fen": board.fen(),
            "orientation": "white" if board.turn else "black",
            "checks": checks,
            "captures": captures,
        },
    )
