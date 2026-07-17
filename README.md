# chesstrain

Analyze your chess.com history for recurring mistakes — especially the
"big think → blunder" pattern that bites when you're winning — and drill those
positions in an interactive, timed trainer that scores each move on a +2..−2
scale.

## What it does

- **Import** your games from chess.com (last *N* games, walking monthly archives).
- **Filter** by color, time control, and outcome.
- **Review** engine-confirmed mistakes and cluster the *recurring* ones (by pawn
  structure, move type, phase, opening).
- **Big-think analytic** — tests whether long thinks lead to more mistakes,
  split by game state (winning / equal / losing).
- **Trainer** — replays your mistake positions; you make a move and get a
  +2..−2 grade, with a time penalty (e.g. blitz −1 after 10s, −2 after 20s).
- **Scout** any opponent's history, and grade *both* sides of your own games.

## Setup

```powershell
uv sync
```

### Stockfish engine (required)

The analysis needs a Stockfish binary. It is **not** committed (GPL-3.0; the app
itself is MIT and drives the engine arm's-length over UCI). Provide one of:

- Place `stockfish.exe` in `engines/` (this repo already vendors it locally), or
- Set `STOCKFISH_PATH` to your own binary.

Download official builds from <https://stockfishchess.org/download/> (Windows
AVX2). The vendored engine's license is in `engines/Stockfish-LICENSE.txt`.

## Usage

```powershell
uv run chesstrain fetch --user hucker233 --n 100     # download last 100 games
uv run chesstrain analyze --user hucker233           # engine analysis (cached)
uv run streamlit run src/chesstrain/app.py           # launch the app
```

Data (raw archives + the SQLite DB) lives in `data/` and is gitignored.
