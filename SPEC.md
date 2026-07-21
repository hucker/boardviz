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
- **ENV-MULTI** Any number of chess.com users can be tracked as profiles in one database; there is no me/opponent split — every imported user is a profile, and exactly one is the *default* that pages open on.

## 3. Domain glossary

- **Mistake** — a move the engine confirms lost significant evaluation *and* had a distinctly-better alternative (so it makes a fair puzzle).
- **Eval drop** — centipawns of evaluation lost by a move (100 cp ≈ one pawn).
- **Grade** — a move's quality, +2 (best) to −2 (blunder), by eval loss vs the best move.
- **Game state** — winning / equal / losing, from the mover's point of view.
- **Structure / Move type / Phase** — coarse tags: centre pawn structure; capture/check/retreat/quiet; opening/middlegame/endgame.
- **Flag loss** — a game lost on time.
- **Time-trouble loss** — a game lost to the clock: an actual flag, *or* a resignation made with my clock critically low and far behind my opponent's (I lost the clock race, so resigning only conceded an imminent flag). The raw termination is left untouched; this is a derived reading of it plus the stored clocks.
- **End state** — winning / even / losing from *my* point of view at the final position, by the engine eval of the last analysed move.
- **Mate chance** — a position where the player had a *forced* checkmate (the engine sees a mate in N). A chance is *converted* if the mate is delivered or held to the end of the game, or *blown* if the player lets it slip.
- **Mate motif** — the pattern of a forced mate: back-rank, smothered, double-check, or the mating piece and where it corners the king (corner/edge/centre).
- **Position** — identified by the board layout (ignoring move-number counters), so the same position recurs across games.

## 4. Functional requirements

### 4.1 Import & analysis (IMP)

- **IMP-FETCH** Fetch the most recent N games for a chess.com username.
- **IMP-TC** Optionally restrict a fetch to one time-control class (bullet/blitz/rapid/daily).
- **IMP-DEFAULT** Every imported user is a profile that any page can select; one is the *default* the app opens on — the first import becomes it automatically, and it can be re-pointed.
- **IMP-DEDUP** Re-fetching is idempotent: already-imported games aren't duplicated and keep their analysed state.
- **IMP-RAWCACHE** Raw fetched JSON is cached on disk per month and pruned to the most recent N files per profile — bounding disk use and, with it, how far back a rebuild can reach.
- **IMP-REBUILD** Rebuild the game corpus from the cached JSON when the database is lost or corrupted; analysis is then re-run to repopulate the derived data.
- **IMP-ANLZ** Run engine analysis over not-yet-analysed games to find mistakes, eval drops, and per-move grades.
- **IMP-BKGND** Analysis runs in the background with live progress; the rest of the app stays usable while it runs.
- **IMP-INCR** Analysis is incremental — results appear per game as they finish.
- **IMP-ENDST** For each analysed game, precompute and store an end-of-game snapshot: my end state (winning/even/losing) and eval, both players' remaining clock, and the piece count at the final position — so it can be filtered and exported without re-deriving it.

### 4.2 Dashboard (DASH)

- **DASH-COUNT** Show summary counts for the filtered games: total, wins, losses, draws, flag losses.
- **DASH-TERM** Show a "how games end" chart splitting wins vs losses by termination method (checkmate, resignation, time, …), so the user sees *how* they win and lose. A resignation lost in a clock race (a time-trouble loss) is grouped next to the actual time-forfeits rather than with board resignations.
- **DASH-TABLE** List the filtered games with date,Color, result, time control, move count, ECO, opening name, flagged/analysed status, and a link to the game.
- **DASH-ENDST** The game table also shows how each game ended (termination method), my end state, both players' remaining clock, and the piece count at the end, so a game resigned or flagged while ahead is visible at a glance; it also notes how many of the filtered games were lost while still winning.
- **DASH-FILT** All dashboard views obey the shared filters (§4.5).

### 4.3 Review (REV)

- **REV-THINK** Show whether long thinks lead to more mistakes, broken down by game state.
- **REV-CLUST** Cluster the player's mistakes by structure, move type, phase, and opening — each with how often it happens, how costly it typically is, and example games.
- **REV-BROWSE** Browse individual mistakes on a board showing the move played vs the engine's best move and line.
- **REV-SIDE** Toggle between the selected profile's own mistakes and their opponents'.
- **REV-GLOSS** Explain the vocabulary (structure/move-type/phase/game-state) inline.

### 4.4 Trainer (TRN)

- **TRN-DRILL** Drill the selected profile's own mistake positions as timed puzzles.
- **TRN-INTRO** Before the clock starts on each puzzle, give a brief fixed pause to get your bearings (the opponent's last move highlighted), and show prominently whichColor you are playing (the board orientation alone can be ambiguous, e.g. in sparse endgames). In **Auto** mode puzzles start and advance hands-free; with Auto off you press Start for each and Next to move on.
- **TRN-NOHINT** Give no hints — the set of legal moves is never revealed.
- **TRN-INPUT** Accept a move by click-then-click or drag; promotions default to a queen.
- **TRN-SCORE** Score each answer by move quality only (time is not counted): +1 for a good move, +0.5 for an inaccuracy, 0 for a blunder, so the total is points out of the positions drilled; and when you miss, make the move's (poor) strength and the engine's best move unmistakable.
- **TRN-ALTS** After answering, let the user click through the position's other good moves to compare them on the board.
- **TRN-ARROW**Color the review arrows by quality: a good move is green, a mistake is red.
- **TRN-MODE** Offer selection modes: random mix, worst blunders first, and repeat-my-misses (previously drilled and failed).
- **TRN-PATRN** Filter the drill by pattern — structure, move type, phase, time control, and opening — in any combination, so a drill can be scoped to one line (e.g. the French Advance); an opening drill can also cap how deep (up to move N) to stay in the opening's structure.
- **TRN-REPEAT** Offer an "only mistakes I've made before" filter (positions blundered 2+ times).
- **TRN-DIFF** Rate each position by find-difficulty — the shallowest search depth at which the engine already sees the best move (precomputed during analysis) — and let the drill filter to the harder finds, skipping the obvious recaptures.
- **TRN-CCT** Offer a CCT drill that trains the pre-move scan **both ways**: on one board you mark the checks, captures, and threats (loose/winnable pieces) available to **you** *and* to your **opponent**, then play your move on that same board (drag or Shift-click). To keep a busy position readable you work **one layer at a time** via Checks / Captures / Threats tabs — only the active layer is shown and markable. In a move layer you click a piece then its target; in Threats you click the loose piece to ring it. Each mark is graded live — ✓ correct, ✗ not that kind — with the side shown by line style (solid = you, dashed = the opponent) and a running per-side count of correct finds. Only after you move (never before, per TRN-NOHINT) does the board reveal each layer's full both-ways set — the ones you missed emphasized, the ones you found faded — alongside a found-vs-total scorecard that calls out how many you missed.
- **TRN-UNIQ** Never show the same position twice in one drill (one puzzle per position).
- **TRN-LEN** Let the user choose the drill length and get a fresh random set each drill.
- **TRN-TALLY** Show a running score (total and average) across the drill.
- **TRN-SOUND** Play short move/start sounds (best effort).

### 4.5 Filters (FLT — shared by Dashboard & Review)

- **FLT-ONE** One filter model scopes Dashboard and Review consistently.
- **FLT-DIMS** Filter by profile, time control, color, result, end state (winning/even/losing), how the game ended (resignation/checkmate/time/…), opening name (substring), ECO code, flagged, and analysis state.
- **FLT-EMPTY** Multi-value filters are multi-select, and an **empty selection means "all"** (no filter).
- **FLT-CLOCK** Filter to "time scrambles" — games whose remaining clock at the end was under a cutoff, choosing whose clock (mine / opponent's / either). The cutoff is an absolute figure (e.g. 5/20/60s) or a fraction of the game's base time control so one setting scales across bullet/blitz/rapid.
- **FLT-TTL** Filter to "time-trouble losses" — games lost to the clock: actual flags plus resignations where my clock was critically low and far behind my opponent's.
- **FLT-RECENT** A "most recent N games" scope narrows the metrics, chart, and table together to the latest N games.
- **FLT-COMPOS** Active filters compose (all apply together).

### 4.6 Mate review (MATE)

- **MATE-DETECT** For each analyzed game, precompute the player's forced-mate chances: the distance (mate-in-N) when the mate first appeared, whether it was converted or blown, the key move, the forced mating line, and a motif — stored so it can be filtered and exported without re-deriving it.
- **MATE-CONV** Show a "mate conversion by distance" chart: for each distance (M1…MX, up to the deepest available), how often the player finished the forced mate versus blew it.
- **MATE-MOTIF** Categorize each mate chance by motif (back-rank, smothered, double-check, mating piece × king location) and let the user see the breakdown and filter by it.
- **MATE-GRID** List the mate chances in a grid the user can click to open the position on a board with the key move highlighted, showing distance, motif, converted/blown, and a link to the game.
- **MATE-FILT** The mate views obey the shared filters (§4.5).

## 5. Non-functional (NFR)

- **NFR-LIVE** The app is usable while analysis runs (reads see partial results).
- **NFR-FAST** The trainer scores instantly, with no engine call at drill time.
- **NFR-CLOCK** Trainer think-time reflects real decision time — measured while you decide, excluding the bearings pause (recorded for reference, not scored).
- **NFR-DETER** Scoring is deterministic for a given position and elapsed time.
- **NFR-WIN** Runs on Windows via uv.

## 6. Out of scope (today)

- Non-chess.com sources; real-time/online play; multiple people using one app instance.

## 7. Working with this spec

The point of the IDs is **traceability** — every requirement can be tied to a test.

- **IDs are stable mnemonics, not numbers.** `TRN-UNIQ` always means "one puzzle per position." New requirements get a new mnemonic; nothing is renumbered, so a v1 test's `TRN-UNIQ` can't drift onto a different requirement later.
- **Reference the ID in the test.** Put it in the test name or docstring, e.g. `# verifies TRN-UNIQ`.
- **Spec-first.** For a behavior change, edit the requirement here first, change the code to match, then add/adjust the tracing test — in the same commit.
- **Auto-loaded.** `CLAUDE.md` points here, so work can be driven by ID ("implement per SPEC.md TRN-SCORE").
- **Keep it user-facing.** Thresholds/algorithms/schema live in code; reference specifics, don't inline them.
- **Audit.** A requirement with no tracing test is either untested or obsolete — grep `verifies [A-Z]+-[A-Z]+` across `test/`.
