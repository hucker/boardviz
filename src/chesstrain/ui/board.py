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


# --- CCT both-ways scan+move board (Custom Components v2) -------------------
# One board that trains the whole pre-move scan AND takes your move. You mark the
# checks / captures / threats available to BOTH sides: click a piece then a
# target draws a move arrow, coloured by whether it is a check / capture / both /
# neither; the *side* of the mark is the colour of the piece you clicked first
# (your piece = your CCT, the opponent's = theirs). Click a piece then the SAME
# piece rings it as a threat (red if it is truly winnable, grey if not). A live
# per-side tally counts only your correct finds. To play your actual move, drag
# your piece or Shift-click the target — that returns the move to Python, which
# scores it. In ``reveal`` mode the board is read-only and dashes in the full
# correct set (both sides) plus your played move — used in the review beat, so no
# answer is shown until after you have moved. All styling is inline.
_BOARD_SCAN_JS = r"""
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const d = data || {};
  const fen = d.fen || "8/8/8/8/8/8/8/8 w - - 0 1";
  const orientation = d.orientation || "white";
  const turn = d.turn || "w";                    // side to move ("me")
  const legal = new Set(d.legal || []);          // your legal moves (for commit)
  const reveal = !!d.reveal;
  const played = d.played || null;               // your move, shown in review
  const lastMove = d.lastMove || null;           // opponent's move into here
  const raw = { me: d.me || {}, opp: d.opp || {} };
  const sets = {};
  for (const k of ["me", "opp"]) {
    sets[k] = { checks: new Set(raw[k].checks || []),
                captures: new Set(raw[k].captures || []),
                threats: new Set(raw[k].threats || []) };
  }

  const COLOR = { check: "#3b82f6", capture: "#f59e0b", both: "#8b5cf6",
                  none: "#9ca3af", threat: "#ef4444", played: "#111827" };
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
  const pieces = parseFen(fen);
  const colorAt = (sq) => {
    const p = pieces[sq]; return p ? (p === p.toUpperCase() ? "w" : "b") : null;
  };
  // Which side a mark belongs to, by the colour of the piece it starts on.
  const sideOfPiece = (sq) => (colorAt(sq) === turn ? "me" : "opp");

  // Square -> [col, row] in the DISPLAY grid (0,0 = top-left), honouring flip.
  const colRow = (sq) => {
    const f = sq.charCodeAt(0) - 97, r = parseInt(sq[1], 10);
    return [white ? f : 7 - f, white ? 8 - r : r - 1];
  };
  const center = (sq) => { const [c, rw] = colRow(sq); return [c + 0.5, rw + 0.5]; };

  const kind = (uci, sideKey) => {
    const s = sets[sideKey];
    const chk = s.checks.has(uci) || s.checks.has(uci + "q");   // auto-queen match
    const cap = s.captures.has(uci) || s.captures.has(uci + "q");
    return chk && cap ? "both" : chk ? "check" : cap ? "capture" : "none";
  };
  // A threat ring on `sq` belongs to whoever can win the piece there: an enemy
  // piece is MY threat, one of my own pieces is the OPPONENT's threat.
  const threatSideOf = (sq) => (colorAt(sq) === turn ? "opp" : "me");
  const resolve = (from, to) => {                 // your move -> validated UCI
    if (legal.has(from + to)) return from + to;
    if (legal.has(from + to + "q")) return from + to + "q";   // auto-queen
    return null;
  };

  // --- build the board grid ---
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
          color: isW ? "#fafafa" : "#1c1c1c",
          textShadow: isW ? "0 0 2px #000, 0 1px 2px #000" : "0 0 2px #d8d8d8" });
        span.textContent = String.fromCodePoint(GLYPH[p.toLowerCase()]);
        // Your own pieces can be dragged to play the move (commit).
        if (!reveal && (isW ? "w" : "b") === turn) {
          span.draggable = true; span.style.cursor = "grab";
          span.addEventListener("dragstart", (e) => {
            clearSel(); e.dataTransfer.setData("text/plain", sq);
          });
        } else {
          span.style.pointerEvents = "none";
        }
        cell.appendChild(span);
      }
      if (lastMove && (sq === lastMove.slice(0, 2) || sq === lastMove.slice(2, 4))) {
        cell.style.boxShadow = "inset 0 0 0 5px rgba(250,204,21,.55)";  // opp's move
      }
      cell.addEventListener("click", (e) => onClick(sq, e.shiftKey));
      cell.addEventListener("dragover", (e) => e.preventDefault());
      cell.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = e.dataTransfer.getData("text/plain");
        if (from && !reveal) { const u = resolve(from, sq); if (u) commit(u); }
      });
      grid.appendChild(cell);
      cells[sq] = cell;
    }
  }
  wrap.appendChild(grid);

  // --- overlay (SVG, board coords 0..8), clicks pass through ---
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
    const op = dashed ? 0.6 : 0.95;
    const line = el("line", { x1, y1, x2: bx, y2: by, stroke: color,
      "stroke-width": 0.16, "stroke-linecap": "round", opacity: op });
    if (dashed) line.setAttribute("stroke-dasharray", "0.28 0.2");
    svg.appendChild(line);
    svg.appendChild(el("polygon", { points:
      `${x2},${y2} ${bx + px * halfw},${by + py * halfw} `
      + `${bx - px * halfw},${by - py * halfw}`, fill: color, opacity: op }));
  };
  const drawRing = (sq, color, dashed) => {
    const [cx, cy] = center(sq);
    const c = el("circle", { cx, cy, r: 0.40, fill: "none", stroke: color,
      "stroke-width": 0.11, opacity: dashed ? 0.7 : 0.95 });
    if (dashed) c.setAttribute("stroke-dasharray", "0.22 0.16");
    svg.appendChild(c);
  };

  // --- marking state ---
  let sel = null;                          // first square clicked
  const arrows = new Map();                // uci -> {from, to, side, kind}
  const rings = new Map();                 // square -> {side, ok}
  const startTime = performance.now();

  const clearSel = () => {
    if (sel) { cells[sel].style.outline = "none"; sel = null; }
  };
  const setSel = (sq) => {
    sel = sq;
    cells[sq].style.outline = "3px solid #4c9be8";
    cells[sq].style.outlineOffset = "-3px";
  };

  const tally = () => {
    const t = { me: { checks: 0, captures: 0, threats: 0 },
                opp: { checks: 0, captures: 0, threats: 0 } };
    for (const a of arrows.values()) {
      const s = sets[a.side], uci = a.from + a.to;
      if (s.checks.has(uci) || s.checks.has(uci + "q")) t[a.side].checks += 1;
      if (s.captures.has(uci) || s.captures.has(uci + "q")) t[a.side].captures += 1;
    }
    for (const r of rings.values()) if (r.ok) t[r.side].threats += 1;
    return t;
  };

  const redraw = () => {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (reveal) {                          // read-only: show the full correct set
      for (const k of ["me", "opp"]) {
        for (const uci of new Set([...sets[k].checks, ...sets[k].captures])) {
          drawArrow(uci.slice(0, 2), uci.slice(2, 4), COLOR[kind(uci, k)], true);
        }
        for (const sq of sets[k].threats) drawRing(sq, COLOR.threat, true);
      }
      if (played) drawArrow(played.slice(0, 2), played.slice(2, 4), COLOR.played, false);
      return;
    }
    for (const a of arrows.values()) drawArrow(a.from, a.to, COLOR[a.kind], false);
    for (const [sq, r] of rings) drawRing(sq, r.ok ? COLOR.threat : COLOR.none, false);
    const t = tally();
    tallyEl.innerHTML =
      `<b>You</b>&nbsp; checks ${t.me.checks} · captures ${t.me.captures} `
      + `· threats ${t.me.threats}<br>`
      + `<b>Opp</b>&nbsp; checks ${t.opp.checks} · captures ${t.opp.captures} `
      + `· threats ${t.opp.threats}`;
  };

  const commit = (uci) => {
    clearSel();
    setTriggerValue("move", { uci: uci,
      ms: Math.max(0, Math.round(performance.now() - startTime)), found: tally() });
  };

  const onClick = (sq, shift) => {
    if (reveal) return;
    if (sel === null) { if (pieces[sq]) setSel(sq); return; }
    const from = sel; cells[from].style.outline = "none"; sel = null;
    if (sq === from) {                     // same piece again -> threat ring
      if (rings.has(sq)) rings.delete(sq);
      else rings.set(sq, { side: threatSideOf(sq),
                           ok: sets[threatSideOf(sq)].threats.has(sq) });
      redraw(); return;
    }
    if (shift && colorAt(from) === turn) { // Shift = play the move
      const u = resolve(from, sq); if (u) { commit(u); return; }
    }
    const uci = from + sq;                  // otherwise a scan arrow
    if (arrows.has(uci)) arrows.delete(uci);
    else arrows.set(uci, { from, to: sq, side: sideOfPiece(from),
                           kind: kind(uci, sideOfPiece(from)) });
    redraw();
  };

  // --- footer: live tally (or legend in reveal) ---
  const foot = document.createElement("div");
  Object.assign(foot.style, { marginTop: "8px", fontSize: "0.85em",
    color: "#6b7280", width: "min(90vmin, 600px)", lineHeight: "1.5" });
  const tallyEl = document.createElement("div");
  foot.appendChild(tallyEl);
  const note = document.createElement("div");
  Object.assign(note.style, { marginTop: "4px" });
  note.innerHTML = reveal
    ? "Everything that was available is shown — dashed arrows are checks/captures, "
      + "red rings are threats, your move is the solid dark arrow."
    : "Blue check · orange capture · purple both · grey none · red ring = threat. "
      + "Arrows from your pieces = your scan, from the opponent's = theirs. Click a "
      + "piece twice to ring a threat. Drag or Shift-click to play your move.";
  foot.appendChild(note);

  parentElement.innerHTML = "";
  parentElement.appendChild(wrap);
  parentElement.appendChild(foot);
  redraw();
}
"""

_BOARD_SCAN = components_v2.component("chesstrain_board_scan", js=_BOARD_SCAN_JS)


def board_scan(board: chess.Board, scan: dict, *, key: str,
               last_move: str | None = None, reveal: bool = False,
               played: str | None = None) -> dict | None:
    """The both-ways CCT board: scan checks/captures/threats and play your move.

    ``scan`` is a :func:`chesstrain.cct.scan_both` result — ``{"me": {...},
    "opp": {...}}`` of ``checks``/``captures`` (UCI) and ``threats`` (square
    names). Click a piece then a target to mark a move (coloured by kind, the
    *side* set by the piece's colour); click a piece twice to ring a threat. A
    running per-side tally counts your correct finds.

    Playing the move (drag a piece or Shift-click a target) returns
    ``{"uci", "ms", "found"}``; it returns ``None`` until you move. In ``reveal``
    mode the board is read-only and shows the full correct set plus your
    ``played`` move — for the review beat, so nothing is revealed until after you
    have answered.
    """
    result = _BOARD_SCAN(
        key=key,
        data=scan_payload(board, scan, last_move=last_move, reveal=reveal,
                          played=played),
        on_move_change=lambda: None,
    )
    return getattr(result, "move", None)


def scan_payload(board: chess.Board, scan: dict, *, last_move: str | None = None,
                 reveal: bool = False, played: str | None = None) -> dict:
    """Build the JSON ``data`` the CCT board consumes from a scan_both result.

    Split out from :func:`board_scan` so the sets → frontend translation can be
    tested without a live component (the CCv2 element only renders in a running
    app). Sets become sorted lists; orientation/turn follow the side to move.
    """
    def _lists(side: dict) -> dict:
        return {"checks": sorted(side.get("checks", [])),
                "captures": sorted(side.get("captures", [])),
                "threats": sorted(side.get("threats", []))}

    return {
        "fen": board.fen(),
        "orientation": "white" if board.turn else "black",
        "turn": "w" if board.turn else "b",
        "legal": [m.uci() for m in board.legal_moves],
        "me": _lists(scan.get("me", {})),
        "opp": _lists(scan.get("opp", {})),
        "lastMove": last_move,
        "reveal": reveal,
        "played": played,
    }
