# boardviz — Product Spec

> Behaviour spec for boardviz: **what the tool does for its user**.
> Requirements carry stable mnemonic IDs (`PREFIX-MNEMONIC`, e.g. `TRN-UNIQ`) so
> tests can trace to them — see [§7 Working with this spec](#7-working-with-this-spec).
> IDs are names, never sequence numbers, so a changed or removed requirement's ID
> is not silently reused by an unrelated one. Implementation details (thresholds,
> algorithms, schema) live in the code; this file is the contract.

## 1. Purpose & audience

boardviz is a local, single-user tool for an amateur who plays on chess.com.
It pulls your games, uses a chess engine to find your recurring mistakes, shows
you the patterns behind them, and lets you drill those exact positions against a
clock — turning "I keep losing" into "here's what to practice."

## 2. Assumptions & constraints (ENV)

- **ENV-LOCAL** Runs locally as a web app (Streamlit) — primarily on the desktop, but the UI stays usable on a small screen / phone (see NFR-COMPACT); no server, accounts, or cloud.
- **ENV-STORE** All data lives in one local SQLite database; nothing is uploaded.
- **ENV-ENGINE** Analysis uses a Stockfish engine the user provides locally.
- **ENV-SOURCE** chess.com's public API is the only game source; fetched games are cached, so the app works offline afterward.
- **ENV-MULTI** Any number of chess.com users can be tracked as profiles in one database; there is no me/opponent split — every imported user is a profile, and exactly one is the *default* that pages open on.
- **ENV-HOSTED** When `BOARDVIZ_HOSTED` is set (non-empty, not `0`), the app runs as a read-only hosted demo: the Import page — and with it every way to fetch games or launch analysis — is not shown at all, and the Dashboard becomes the landing page. No data processing happens in the cloud; visitors explore and drill the shipped sample.
- **ENV-DEMO** A try-it fallback is the one exception to "local only": when the database is absent *or empty* (no profiles and no games — e.g. a bare clone, or a schema created before any import), the app downloads a zipped sample database (by default the latest release asset) at startup, so a fresh checkout or hosted demo (Streamlit Community Cloud) boots with games to explore. `BOARDVIZ_SAMPLE_URL` points the fallback elsewhere; setting it to an empty string disables it. A failed download never blocks startup — the app just starts empty.

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

- **IMP-FETCH** Fetch the most recent N games for a chess.com username. Only standard chess is imported — variants (Chess960, etc.) are skipped, since the analysis assumes standard chess. Each game records the site it came from (a `source` of chess.com / lichess), stored on the game.
- **IMP-LICHESS** Fetch the most recent N games for a *lichess* username as well, via the lichess PGN export — the same import flow (POV, outcome, opening, time control, clocks) parsed from PGN headers. The source is recorded in the stored game URL, so the rest of the app can tell a lichess game from a chess.com one (e.g. the game-info source badge).
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
- **DASH-TABLE** List the filtered games with date, colour, result, time control, move count, ECO, opening name, flagged/analysed status, and a link to the game.
- **DASH-ENDST** The game table also shows how each game ended (termination method), my end state, both players' remaining clock, and the piece count at the end, so a game resigned or flagged while ahead is visible at a glance; it also notes how many of the filtered games were lost while still winning.
- **DASH-FILT** All dashboard views obey the shared filters (§4.5).

### 4.3 Review (REV)

- **REV-THINK** Show whether long thinks lead to more mistakes, broken down by game state.
- **REV-CLUST** Cluster the player's mistakes by structure, move type, phase, and opening — each with how often it happens, how costly it typically is, and example games.
- **REV-BROWSE** Browse individual mistakes on a board showing the move played vs the engine's best move and line.
- **REV-SIDE** Toggle between the selected profile's own mistakes and their opponents'.
- **REV-GLOSS** Explain the vocabulary (structure/move-type/phase/game-state) inline.

### 4.4 Trainer (TRN)

- **TRN-DRILL** Drill the selected profile's own mistake positions as self-paced puzzles. You play your move on the board (click a piece then its target, or drag it); a move that promotes offers a piece picker (Q / R / B / N), so under-promotions (e.g. b1=N#) are playable and never silently auto-queened.
- **TRN-INTRO** Each position shows with the opponent's last move highlighted and, prominently, which colour you are playing (the board orientation alone can be ambiguous, e.g. in sparse endgames), so you can orient before choosing your move. The board you play on labels ranks and files on **all four edges**, so squares are readable without decoding notation. The drill is **self-paced** — no timer, no auto-start or auto-advance — and you press Next to move on.
- **TRN-NOHINT** Give no hints — the set of legal moves is never revealed.
- **TRN-INPUT** Accept a move by click-then-click or drag; promotions default to a queen.
- **TRN-SCORE** Score each answer by move quality only (time is not counted): +1 for a good move, +0.5 for an inaccuracy, 0 for a blunder, so the total is points out of the positions drilled; and when you miss, make the move's (poor) strength and the engine's best move unmistakable. Show each answered position's result graphically — a correct / inaccuracy / missed badge — rather than a bare number.
- **TRN-ALTS** After answering, the best move and your move are always drawn on the board; clicking any of the position's other good moves toggles it on the board (in grey) so you can compare.
- **TRN-ARROW** Colour the review arrows so the outcome is clear at a glance: the best move is always green (so you can see the move you should have played); your move is drawn too — the same green when you played the best, otherwise black — and any move you clicked to compare is grey.
- **TRN-MODE** Offer selection modes: random mix, worst blunders first, and repeat-my-misses (previously drilled and failed).
- **TRN-PATRN** Filter the drill by pattern — structure, move type, phase, time control, and opening — in any combination, so a drill can be scoped to one line (e.g. the French Advance); an opening drill can also cap how deep (up to move N) to stay in the opening's structure.
- **TRN-REPEAT** Offer an "only mistakes I've made before" filter (positions blundered 2+ times).
- **TRN-DIFF** Rate each position by find-difficulty — the shallowest search depth at which the engine already sees the best move (precomputed during analysis) — and let the drill filter to the harder finds, skipping the obvious recaptures.
- **TRN-CCT** The drill style is an explicit, visible choice between **Check/Cap/Threat** (the scan drill) and **Best move** (play the move, no scan) — not a buried toggle — and Check/Cap/Threat is the default. The CCT drill trains the pre-move scan **both ways**: on one board you mark the checks, captures, and threats — a **threat** being a piece winnable *right now* (hanging, or a favourable exchange where your cheapest attacker is worth less): a static one-ply **material** check, **not** an engine's tactical search, so discovered attacks, forks and deeper combinations are out of scope — available to **you** *and* to your **opponent**, then play your move on that same board (drag or Shift-click). To keep a busy position readable you work **one layer at a time** via Checks / Captures / Threats tabs — only the active layer is shown and markable. In a move layer you click a piece then its target; in Threats you click the loose piece to ring it. A correct mark sticks with a ✓; a wrong one is rejected — it buzzes and shows a short note on why, and never sticks (the point is to *identify*, not guess) — with the side shown by line style (solid = you, dashed = the opponent) and a running per-side count of correct finds. A move that is both a check and a capture, marked as a **check**, auto-adds the capture (a checking capture is obviously a capture); a **mutual capture** — where the two pieces take each other — marks both sides; but a capture is never auto-marked as a check, since spotting that a capture gives check is the identification skill being trained. And the move you actually **play**, if it is itself a check or capture, counts as found even if you forgot to mark it — playing a forcing move proves you saw it. Only after you move (never before, per TRN-NOHINT) does the board reveal each layer's full both-ways set — the ones you missed emphasized, the ones you found faded — and a compact table lists exactly what you missed in algebraic notation (SAN for checks/captures, piece + square for threats). A **running scoreboard** tallies the drill as seven found-vs-available scores — the six categories (you/opponent × checks/captures/threats) plus a grand total. Each position is scored out of four: for each category (checks, captures, threats — both sides) **the fraction you found** — so a partial scan earns partial credit rather than all-or-nothing (a category with nothing to find is trivially complete) — **plus the move score**, and the drill's running m/n is shown green while every position stays perfect. A **clean scan** (every item found) is celebrated when you also play a good move.
- **TRN-MATE** Offer mate-drill modes over the profile's own forced-mate chances (see MATE-DETECT): **M1** shows a position where a single move checkmates — scored by *delivering* the mate (any mating move counts); **M2+** shows a deeper forced mate — scored by *finding the key move* that forces it. A filter drills only the mates you missed (blown) or all of them; one point per solved position, and no engine at drill time.
- **TRN-UNIQ** Never show the same position twice in one drill (one puzzle per position).
- **TRN-LEN** Let the user choose the drill length and get a fresh random set each drill.
- **TRN-TALLY** Show the drill's running score as a graphic — one cell per position, coloured correct / inaccuracy / missed / still-to-come, with the correct-so-far count out of the drill length. The board and its explanatory text sit on the left; the running (Challenge) and this-position (Puzzle) scores are boxed on the right.
- **TRN-CONTEXT** Make each drilled position traceable to the game it came from and cite its source. A clean header names only the position and the colour you play; a single `?` tooltip carries everything else — what to do, the two players, date, time control and opening, a source-badged link to the game (chess.com / lichess, read from the game URL — keeping the app source-aware ahead of a lichess importer), and the copyable FEN / EPD (each label linking out to an explanation of the notation) — so the exact position can be reproduced. Never a hint (the position is already on the board).
- **TRN-SOUND** Play short move/start sounds (best effort).

### 4.5 Filters (FLT — shared by Dashboard & Review)

- **FLT-ONE** One filter model scopes Dashboard and Review consistently.
- **FLT-DIMS** Filter by profile, source (chess.com / lichess), time control, color, result, end state (winning/even/losing), how the game ended (resignation/checkmate/time/…), opening name (substring), ECO code, flagged, and analysis state. The trainer drills can also be scoped by source.
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
- **NFR-DETER** Scoring is deterministic for a given position and answer.
- **NFR-WIN** Runs on Windows via uv.
- **NFR-COMPACT** The UI stays compact and usable on a small screen / phone: minimal wasted chrome (e.g. trimmed top padding, no oversized headers), context folded into tooltips rather than stacked lines, and content that packs from the top instead of spreading across a wide page.

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
