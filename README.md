# boardviz

Train your chess **board vision** on your own games. boardviz pulls your games
from chess.com or lichess, uses Stockfish to find the mistakes you actually
make, shows you the patterns behind them, and drills those exact positions —
so "I keep losing" turns into "here's what to practice."

> **Try it first:** the hosted demo boots with a library of pre-analyzed games,
> so you can explore every page before installing anything. *(link — see below)*

<!-- SCREENSHOT: trainer-cct.png — the CCT scan drill mid-position (a board with
     a few check/capture/loose marks), showing the right-hand scoreboard. -->

## What it does

- **Board-vision drill (the core).** Before you move, scan the position the way
  a strong player does — the **checks**, **captures**, and **loose pieces**
  (material you can win *right now*: hanging, or a favourable trade) — for **both
  sides**. You mark them on the board one layer at a time; a wrong mark buzzes
  and won't stick, because the point is to *identify*, not guess. Then you play
  your move on the same board. Scope the scan to **both sides**, **just yours**,
  or **just the opponent's** — opponent-only is the pure pre-move blunder-check
  ("what can they do to me?").
- **A mistake library from your own games.** Stockfish analyses every game and
  records each real mistake (a move that lost significant evaluation *and* had a
  clearly better alternative), so the drill positions are *your* actual
  blunders, not generic puzzles.
- **Mate drills.** Practise the forced mates you had — deliver the mate-in-1s,
  find the key move in the deeper ones, and re-drill the ones you blew.
- **Review & clustering.** See your *recurring* mistakes grouped by pawn
  structure, move type, phase, and opening — each with how often it happens, how
  costly it is, and example games. Includes smaller analytics like whether long
  thinks lead to more mistakes, split by whether you were winning or losing.
- **Dashboard.** Win/loss/draw counts, how your games end (checkmate /
  resignation / time), and a game table that flags the ones you lost while still
  winning.
- **Multi-source, multi-profile.** Import from chess.com *and* lichess; track
  any number of players as profiles in one database.

The drill is **self-paced** — no timer, no auto-advance — and each answer is
scored on move quality (good move, inaccuracy, or blunder), with the engine's
best move drawn on the board when you miss.

## Install & run (uv)

boardviz uses [uv](https://docs.astral.sh/uv/). If you don't have it, install it
first (one command from the uv docs — no Python setup needed; uv fetches its
own).

```powershell
uv sync            # install dependencies
uv run boardviz    # launch the app
```

On first launch with no database, boardviz offers to download a sample library
so you have games to explore immediately.

### Stockfish engine (needed only to analyse your own games)

Browsing, drilling, and the sample library need **no engine**. You only need
Stockfish to import and analyse *your own* games. It is **not** bundled
(Stockfish is GPL-3.0; boardviz is MIT and drives it arm's-length over UCI):

- Place `stockfish` / `stockfish.exe` in `engines/`, **or**
- Set `STOCKFISH_PATH` to your own binary.

Download official builds from <https://stockfishchess.org/download/> (on
Windows, the AVX2 build). The engine's own license, once vendored, sits in
`engines/`.

### Import your games (command line)

```powershell
uv run boardviz fetch --user <chesscom-name> --n 100   # download last 100 games
uv run boardviz analyze --user <chesscom-name>         # Stockfish analysis (cached)
uv run boardviz rebuild                                 # rebuild DB from cached JSON
```

Importing and analysis are also available from the app's **Import** page. Data
(raw archives + the SQLite database) lives in `data/` and is gitignored —
nothing is uploaded.

## Hosted demo (Streamlit Community Cloud)

The app runs as a read-only demo on Streamlit Community Cloud. There, the
**Import page is disabled** — the cloud has no chess engine and its storage is
shared and temporary, so fetching and analysing your own games isn't available.
The demo exists to *show* the app over a shared sample library; to work on your
own games, install and run it locally as above.

<!-- SCREENSHOT: dashboard.png — the Dashboard over the sample library
     (counts + "how games end" chart + a few table rows). -->

<!-- SCREENSHOT: review-clusters.png — the Review page showing mistake clusters
     by structure/opening with counts and average cost. -->

## Screenshots wanted

The `<!-- SCREENSHOT: ... -->` markers above are placeholders. If you grab
those PNGs (drop them in a `docs/img/` folder), I'll wire them in.
