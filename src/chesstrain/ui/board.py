"""Board rendering (python-chess SVG) and legal-move input helpers.

SVG is shown via ``st.components.v1.html`` (st.image can't render SVG). Move
input is a SAN dropdown of legal moves — illegal-move-proof and rerun-safe, no
native chessboard dependency.
"""

from __future__ import annotations

from collections.abc import Iterable

import chess
import chess.svg
import streamlit.components.v1 as components
import streamlit.components.v2 as components_v2


def board_svg(board: chess.Board, *, size: int = 380,
              lastmove: chess.Move | None = None,
              arrows: Iterable = (), orientation: bool | None = None) -> str:
    """Render `board` to an SVG string, oriented to the side to move by default."""
    return chess.svg.board(
        board, size=size, lastmove=lastmove, arrows=list(arrows),
        orientation=board.turn if orientation is None else orientation,
    )


def show_board(board: chess.Board, *, size: int = 380,
               lastmove: chess.Move | None = None,
               arrows: Iterable = (), orientation: bool | None = None) -> None:
    """Render a board into the current Streamlit container."""
    svg = board_svg(board, size=size, lastmove=lastmove, arrows=arrows,
                    orientation=orientation)
    components.html(f'<div style="display:flex">{svg}</div>', height=size + 12)


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
  width: min(72vmin, 440px);
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
  font-size: min(9.5vmin, 56px);
  line-height: 1;
  z-index: 1;
}
.ct-piece.own { cursor: grab; }
.ct-piece.w { color: #fafafa; text-shadow: 0 0 2px #000, 0 1px 2px #000; }
.ct-piece.b { color: #1c1c1c; text-shadow: 0 0 2px #d8d8d8; }
.ct-sq.sel { outline: 3px solid var(--st-primary-color, #4c9be8); outline-offset: -3px; }
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
  const parts = fen.split(" ");
  const sideToMove = parts[1] || "w";

  // Filled glyphs for both colors (crisper than outline glyphs on a board);
  // color is carried by CSS, not the codepoint.
  const GLYPH = {k:0x265A, q:0x265B, r:0x265C, b:0x265D, n:0x265E, p:0x265F};

  const pieces = {};
  const ranks = parts[0].split("/");           // rank 8 first
  for (let r = 0; r < 8; r++) {
    let file = 0;
    for (const ch of ranks[r] || "") {
      if (ch >= "1" && ch <= "8") { file += parseInt(ch, 10); }
      else { pieces["abcdefgh"[file] + (8 - r)] = ch; file += 1; }
    }
  }

  const files = "abcdefgh".split("");
  const rankOrder = orientation === "white" ? [8,7,6,5,4,3,2,1] : [1,2,3,4,5,6,7,8];
  const fileOrder = orientation === "white" ? files : files.slice().reverse();

  const isOwn = (p) => !!p && (sideToMove === "w"
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

  parentElement.innerHTML = "";
  const board = document.createElement("div");
  board.className = "ct-board";
  parentElement.appendChild(board);

  const cells = {};
  let selected = null;
  let submitted = false;

  const clearHints = () => Object.values(cells).forEach(
    c => c.classList.remove("sel", "hint", "cap"));
  const select = (sq) => {
    selected = sq;
    clearHints();
    cells[sq].classList.add("sel");
    targetsOf(sq).forEach(to => {
      if (cells[to]) cells[to].classList.add(pieces[to] ? "cap" : "hint");
    });
  };
  const tryMove = (from, to) => {
    if (submitted) return false;
    const uci = resolve(from, to);
    if (!uci) return false;
    submitted = true;
    clearHints();
    setTriggerValue("move", uci);
    return true;
  };

  for (const rank of rankOrder) {
    for (const f of fileOrder) {
      const sq = f + rank;
      const cell = document.createElement("div");
      cell.className = "ct-sq " + (("abcdefgh".indexOf(f) + rank) % 2 === 0
        ? "light" : "dark");
      const p = pieces[sq];
      if (p) {
        const span = document.createElement("span");
        const own = isOwn(p);
        span.className = "ct-piece " + (p === p.toUpperCase() ? "w" : "b")
          + (own ? " own" : "");
        span.textContent = String.fromCodePoint(GLYPH[p.toLowerCase()]);
        if (own) {
          span.draggable = true;
          span.addEventListener("dragstart", (e) => {
            select(sq);
            e.dataTransfer.setData("text/plain", sq);
          });
        }
        cell.appendChild(span);
      }
      cell.addEventListener("click", () => {
        if (submitted) return;
        if (selected && selected !== sq && tryMove(selected, sq)) return;
        if (isOwn(pieces[sq])) select(sq);
        else { clearHints(); selected = null; }
      });
      cell.addEventListener("dragover", (e) => e.preventDefault());
      cell.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = e.dataTransfer.getData("text/plain");
        if (from) tryMove(from, sq);
      });
      board.appendChild(cell);
      cells[sq] = cell;
    }
  }
}
"""

_BOARD_INPUT = components_v2.component(
    "chesstrain_board_input",
    css=_BOARD_INPUT_CSS,
    js=_BOARD_INPUT_JS,
)


def board_input(board: chess.Board, *, key: str) -> str | None:
    """Interactive move-entry board; returns the UCI the user played, or None.

    Click a piece then a square, or drag it — both work. Promotions auto-queen.
    The legal-move list is sent to the frontend only to reject illegal moves; it
    is never shown, so nothing tells you which piece to move.
    """
    result = _BOARD_INPUT(
        key=key,
        data={
            "fen": board.fen(),
            "orientation": "white" if board.turn else "black",
            "legal": [m.uci() for m in board.legal_moves],
        },
        on_move_change=lambda: None,
    )
    return getattr(result, "move", None)
