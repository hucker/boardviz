# chesstrain — Product Spec

> Behaviour spec for chesstrain: **what the tool does for its user**.
> Requirements carry stable mnemonic IDs (`PREFIX-MNEMONIC`, e.g. `TRN-UNIQ`) so
> tests can trace to them — see [§7 Working with this spec](#7-working-with-this-spec).
> IDs are names, never sequence numbers, so a changed or removed requirement's ID
> is not silently reused by an unrelated one. Implementation details (thresholds,
> algorithms, schema) live in the code; this file is the contract.

## 1. Purpose & audience

chesstrain is a local, single-user tool for an amateur who plays on chess.com.
It pulls your games, uses a chess engine to find your recurring mistakes, shows
you the patterns behind them, and lets you drill those exact positions against a
clock — turning "I keep losing" into "here's what to practice."

## 2. Assumptions & constraints (ENV)

- **ENV-LOCAL** Runs locally as a desktop web app (Streamlit); no server, accounts, or cloud.
- **ENV-STORE** All data lives in one local SQLite database; nothing is uploaded.
- **ENV-ENGINE** Analysis uses a Stockfish engine the user provides locally.
- **ENV-SOURCE** chess.com's public API is the only game source; fetched games are cached, so the app works offline afterward.
- **ENV-SOLO** One active player ("me") plus any number of scouted opponents.

## 3. Domain glossary

- **Mistake** — a move the engine confirms lost significant evaluation *and* had a distinctly-better alternative (so it makes a fair puzzle).
- **Eval drop** — centipawns of evaluation lost by a move (100 cp ≈ one pawn).
- **Grade** — a move's quality, +2 (best) to −2 (blunder), by eval loss vs the best move.
- **Game state** — winning / equal / losing, from the mover's point of view.
- **Structure / Move type / Phase** — coarse tags: centre pawn structure; capture/check/retreat/quiet; opening/middlegame/endgame.
- **Flag loss** — a game lost on time.
- **End state** — winning / even / losing from *my* point of view at the final position, by the engine eval of the last analysed move.
- **Position** — identified by the board layout (ignoring move-number counters), so the same position recurs across games.

## 4. Functional requirements

### 4.1 Import & analysis (IMP)

- **IMP-FETCH** Fetch the most recent N games for a chess.com username.
- **IMP-TC** Optionally restrict a fetch to one time-control class (bullet/blitz/rapid/daily).
- **IMP-SCOUT** "Scout" mode stores the fetched player as an opponent, not as me.
- **IMP-DEDUP** Re-fetching is idempotent: already-imported games aren't duplicated and keep their analysed state.
- **IMP-ANLZ** Run engine analysis over not-yet-analysed games to find mistakes, eval drops, and per-move grades.
- **IMP-BKGND** Analysis runs in the background with live progress; the rest of the app stays usable while it runs.
- **IMP-INCR** Analysis is incremental — results appear per game as they finish.
- **IMP-ENDST** For each analysed game, precompute and store an end-of-game snapshot: my end state (winning/even/losing) and eval, both players' remaining clock, and the piece count at the final position — so it can be filtered and exported without re-deriving it.

### 4.2 Dashboard (DASH)

- **DASH-COUNT** Show summary counts for the filtered games: total, wins, losses, draws, flag losses.
- **DASH-TERM** Show a "how games end" chart splitting wins vs losses by termination method (checkmate, resignation, time, …), so the user sees *how* they win and lose.
- **DASH-TABLE** List the filtered games with date, colour, result, time control, move count, ECO, opening name, flagged/analysed status, and a link to the game.
- **DASH-ENDST** The game table also shows how each game ended (termination method), my end state, both players' remaining clock, and the piece count at the end, so a game resigned or flagged while ahead is visible at a glance; it also notes how many of the filtered games were lost while still winning.
- **DASH-FILT** All dashboard views obey the shared filters (§4.6).

### 4.3 Review (REV)

- **REV-THINK** Show whether long thinks lead to more mistakes, broken down by game state.
- **REV-CLUST** Cluster the player's mistakes by structure, move type, phase, and opening — each with how often it happens, how costly it typically is, and example games.
- **REV-BROWSE** Browse individual mistakes on a board showing the move played vs the engine's best move and line.
- **REV-SIDE** Toggle between the player's mistakes and the opponent's.
- **REV-GLOSS** Explain the vocabulary (structure/move-type/phase/game-state) inline.

### 4.4 Trainer (TRN)

- **TRN-DRILL** Drill the player's own mistake positions as timed puzzles.
- **TRN-INTRO** Before each puzzle, replay the opponent's move that led into the position at roughly their real pace; the clock starts only after.
- **TRN-NOHINT** Give no hints — the set of legal moves is never revealed.
- **TRN-INPUT** Accept a move by click-then-click or drag; promotions default to a queen.
- **TRN-SCORE** Score each answer +2..−2, combining move quality with a time penalty (slower scores lower), and show the engine's best move.
- **TRN-ALTS** After answering, let the user click through the position's other good moves to compare them on the board.
- **TRN-ARROW** Colour the review arrows by quality: a good move is green, a mistake is red.
- **TRN-MODE** Offer selection modes: random mix, worst blunders first, and repeat-my-misses (previously drilled and failed).
- **TRN-PATRN** Filter the drill by pattern — structure, move type, phase, time control — in any combination.
- **TRN-REPEAT** Offer an "only mistakes I've made before" filter (positions blundered 2+ times).
- **TRN-UNIQ** Never show the same position twice in one drill (one puzzle per position).
- **TRN-LEN** Let the user choose the drill length and get a fresh random set each drill.
- **TRN-TALLY** Show a running score (total and average) across the drill.
- **TRN-SOUND** Play short move/start sounds (best effort).

### 4.5 Scout (SCT)

- **SCT-FETCH** Fetch and analyse any chess.com user as an opponent.
- **SCT-VIEW** View that opponent's recurring mistakes via the Review analytics, to prep against them.

### 4.6 Filters (FLT — shared by Dashboard & Review)

- **FLT-ONE** One filter model scopes Dashboard and Review consistently.
- **FLT-DIMS** Filter by profile, time control, colour, result, end state (winning/even/losing), opening name (substring), ECO code, flagged, and analysis state.
- **FLT-EMPTY** Multi-value filters are multi-select, and an **empty selection means "all"** (no filter).
- **FLT-RECENT** A "most recent N games" scope narrows the metrics, chart, and table together to the latest N games.
- **FLT-COMPOS** Active filters compose (all apply together).

## 5. Non-functional (NFR)

- **NFR-LIVE** The app is usable while analysis runs (reads see partial results).
- **NFR-FAST** The trainer scores instantly, with no engine call at drill time.
- **NFR-CLOCK** Trainer think-time reflects real decision time — measured while you decide, excluding the intro replay.
- **NFR-DETER** Scoring is deterministic for a given position and elapsed time.
- **NFR-WIN** Runs on Windows via uv.

## 6. Out of scope (today)

- Drilling opponent mistakes in the trainer (would require grading their positions).
- Non-chess.com sources; real-time/online play; multi-user.

## 7. Working with this spec

The point of the IDs is **traceability** — every requirement can be tied to a test.

- **IDs are stable mnemonics, not numbers.** `TRN-UNIQ` always means "one puzzle per position." New requirements get a new mnemonic; nothing is renumbered, so a v1 test's `TRN-UNIQ` can't drift onto a different requirement later.
- **Reference the ID in the test.** Put it in the test name or docstring, e.g. `# verifies TRN-UNIQ`.
- **Spec-first.** For a behaviour change, edit the requirement here first, change the code to match, then add/adjust the tracing test — in the same commit.
- **Auto-loaded.** `CLAUDE.md` points here, so work can be driven by ID ("implement per SPEC.md TRN-SCORE").
- **Keep it user-facing.** Thresholds/algorithms/schema live in code; reference specifics, don't inline them.
- **Audit.** A requirement with no tracing test is either untested or obsolete — grep `verifies [A-Z]+-[A-Z]+` across `test/`.
