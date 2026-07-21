# Traceability matrix

> Generated from `SPEC.md` + `test/` by `test/test_spec_traceability.py` — do not edit by hand. Regenerate with `uv run python test/test_spec_traceability.py`.

**58 requirements — 37 tested, 21 not unit-tested** (environment facts, browser-side UI, audio, and network fetch).

## ENV — Environment & constraints

| Requirement | Behavior | Tests |
|---|---|---|
| **ENV-LOCAL** | Runs locally as a desktop web app (Streamlit); no server, accounts, or cloud. | — _not unit-tested_ |
| **ENV-STORE** | All data lives in one local SQLite database; nothing is uploaded. | — _not unit-tested_ |
| **ENV-ENGINE** | Analysis uses a Stockfish engine the user provides locally. | — _not unit-tested_ |
| **ENV-SOURCE** | chess.com's public API is the only game source; fetched games are cached, so the app works offline afterward. | — _not unit-tested_ |
| **ENV-SOLO** | One active player ("me") plus any number of scouted opponents. | — _not unit-tested_ |

## IMP — Import & analysis

| Requirement | Behavior | Tests |
|---|---|---|
| **IMP-FETCH** | Fetch the most recent N games for a chess.com username. | `test_archive_url_year_month_is_parsed`, `test_load_games_classifies_pov_and_result`, `test_months_between_is_inclusive`, `test_payload_orients_and_flips_turn_for_black` |
| **IMP-TC** | Optionally restrict a fetch to one time-control class (bullet/blitz/rapid/daily). | `test_tc_class_boundaries`, `test_tc_class_handles_untimed_and_empty` |
| **IMP-SCOUT** | "Scout" mode stores the fetched player as an opponent, not as me. | `test_scout_import_stores_the_user_as_an_opponent` |
| **IMP-DEDUP** | Re-fetching is idempotent: already-imported games aren't duplicated and keep their analysed state. | `test_reimporting_the_same_game_inserts_nothing` |
| **IMP-ANLZ** | Run engine analysis over not-yet-analysed games to find mistakes, eval drops, and per-move grades. | `test_clean_win_has_no_mistake_of_mine_and_snapshots_the_win`, `test_every_game_is_analyzed_with_per_move_rows`, `test_missed_tactic_is_flagged_as_my_mistake`, `test_my_mistake_is_cached_as_a_trainer_puzzle` |
| **IMP-BKGND** | Analysis runs in the background with live progress; the rest of the app stays usable while it runs. | `test_import_run_row_tracks_progress` |
| **IMP-INCR** | Analysis is incremental — results appear per game as they finish. | `test_marking_a_game_analysed_removes_it_from_pending` |
| **IMP-ENDST** | For each analysed game, precompute and store an end-of-game snapshot: my end state (winning/even/losing) and eval, both players' remaining clock, and the piece count at the final position — so it can be filtered and exported without re-deriving it. | `test_backfill_fills_analysed_games_missing_the_snapshot`, `test_state_buckets_by_the_win_threshold`, `test_store_flips_the_final_ply_to_my_pov_and_captures_context` |

## DASH — Dashboard

| Requirement | Behavior | Tests |
|---|---|---|
| **DASH-COUNT** | Show summary counts for the filtered games: total, wins, losses, draws, flag losses. | `test_payload_orients_and_flips_turn_for_black`, `test_summary_counts_accept_a_list_filter` |
| **DASH-TERM** | Show a "how games end" chart splitting wins vs losses by termination method (checkmate, resignation, time, …), so the user sees *how* they win and lose. A resignation lost in a clock race (a time-trouble loss) is grouped next to the actual time-forfeits rather than with board resignations. | `test_classify_termination_maps_outcome_and_method`, `test_resign_bucket_flips_eval_to_the_resigner_pov`, `test_termination_breakdown_splits_resignations` |
| **DASH-TABLE** | List the filtered games with date, colour, result, time control, move count, ECO, opening name, flagged/analysed status, and a link to the game. | — _not unit-tested_ |
| **DASH-ENDST** | The game table also shows how each game ended (termination method), my end state, both players' remaining clock, and the piece count at the end, so a game resigned or flagged while ahead is visible at a glance; it also notes how many of the filtered games were lost while still winning. | — _not unit-tested_ |
| **DASH-FILT** | All dashboard views obey the shared filters (§4.6). | `test_summary_counts_obey_the_active_filter` |

## REV — Review

| Requirement | Behavior | Tests |
|---|---|---|
| **REV-THINK** | Show whether long thinks lead to more mistakes, broken down by game state. | `test_game_state_thresholds` |
| **REV-CLUST** | Cluster the player's mistakes by structure, move type, phase, and opening — each with how often it happens, how costly it typically is, and example games. | `test_classify_move_type_detects_a_retreat`, `test_classify_move_type_ranks_capture_check_over_quiet`, `test_payload_orients_and_flips_turn_for_black`, `test_phase_of_splits_opening_middlegame_endgame` |
| **REV-BROWSE** | Browse individual mistakes on a board showing the move played vs the engine's best move and line. | — _not unit-tested_ |
| **REV-SIDE** | Toggle between the player's mistakes and the opponent's. | — _not unit-tested_ |
| **REV-GLOSS** | Explain the vocabulary (structure/move-type/phase/game-state) inline. | — _not unit-tested_ |

## TRN — Trainer

| Requirement | Behavior | Tests |
|---|---|---|
| **TRN-DRILL** | Drill the player's own mistake positions as timed puzzles. | `test_payload_orients_and_flips_turn_for_black` |
| **TRN-INTRO** | Before the clock starts on each puzzle, give a brief fixed pause to get your bearings (the opponent's last move highlighted), and show prominently which colour you are playing (the board orientation alone can be ambiguous, e.g. in sparse endgames). In **Auto** mode puzzles start and advance hands-free; with Auto off you press Start for each and Next to move on. | `test_bearings_pause_highlights_the_opponent_last_move`, `test_bearings_pause_when_there_is_no_prior_move`, `test_side_line_names_black_when_black_is_to_move`, `test_side_line_names_white_when_white_is_to_move` |
| **TRN-NOHINT** | Give no hints — the set of legal moves is never revealed. | — _not unit-tested_ |
| **TRN-INPUT** | Accept a move by click-then-click or drag; promotions default to a queen. | — _not unit-tested_ |
| **TRN-SCORE** | Score each answer by move quality only (time is not counted): +1 for a good move, +0.5 for an inaccuracy, 0 for a blunder, so the total is points out of the positions drilled; and when you miss, make the move's (poor) strength and the engine's best move unmistakable. | `test_commit_scores_and_records_the_attempt`, `test_score_is_move_quality_only`, `test_win_loss_readout_phrasing` |
| **TRN-ALTS** | After answering, let the user click through the position's other good moves to compare them on the board. | — _not unit-tested_ |
| **TRN-ARROW** | Colour the review arrows by quality: a good move is green, a mistake is red. | — _not unit-tested_ |
| **TRN-MODE** | Offer selection modes: random mix, worst blunders first, and repeat-my-misses (previously drilled and failed). | `test_default_mode_returns_the_whole_pool`, `test_repeat_failures_mode_keeps_only_positions_failed_before`, `test_worst_mode_orders_by_biggest_eval_drop` |
| **TRN-PATRN** | Filter the drill by pattern — structure, move type, phase, time control, and opening — in any combination, so a drill can be scoped to one line (e.g. the French Advance); an opening drill can also cap how deep (up to move N) to stay in the opening's structure. | `test_each_pattern_dimension_narrows_the_pool`, `test_max_fullmove_caps_the_drill_to_the_early_opening`, `test_opening_filter_scopes_the_drill_to_one_line`, `test_pattern_filters_compose` |
| **TRN-REPEAT** | Offer an "only mistakes I've made before" filter (positions blundered 2+ times). | `test_repeated_only_keeps_positions_blundered_more_than_once` |
| **TRN-DIFF** | Rate each position by find-difficulty — the shallowest search depth at which the engine already sees the best move (precomputed during analysis) — and let the drill filter to the harder finds, skipping the obvious recaptures. | `test_difficulty_filter_keeps_only_the_harder_finds` |
| **TRN-CCT** | Offer a CCT drill that trains the pre-move scan **both ways**: on one board you mark the checks, captures, and threats (loose/winnable pieces) available to **you** *and* to your **opponent**, then play your move on that same board (drag or Shift-click). To keep a busy position readable you work **one layer at a time** via Checks / Captures / Threats tabs — only the active layer is shown and markable. In a move layer you click a piece then its target; in Threats you click the loose piece to ring it. Each mark is graded live — ✓ correct, ✗ not that kind — with the side shown by line style (solid = you, dashed = the opponent) and a running per-side count of correct finds. Only after you move (never before, per TRN-NOHINT) does the board reveal each layer's full both-ways set — the ones you missed emphasised, the ones you found faded — alongside a found-vs-total scorecard that calls out how many you missed. | `test_a_capturing_check_lands_in_both_sets`, `test_checks_and_captures_are_split_out`, `test_commit_carries_the_cct_marks`, `test_counts_correct_marks_and_ignores_wrong_ones`, `test_defended_equal_piece_is_not_a_threat`, `test_each_side_gets_its_own_check`, `test_enemy_king_is_never_a_threat`, `test_hanging_enemy_piece_is_a_threat`, `test_king_attacker_on_a_defended_piece_does_not_win_it`, `test_king_wins_an_undefended_adjacent_piece`, `test_opponent_scan_is_empty_when_in_check`, `test_quiet_position_has_no_forcing_moves`, `test_quiet_position_has_no_threats`, `test_summary_celebrates_a_clean_scan`, `test_summary_reports_finds_over_totals_for_both_sides`, `test_threats_point_at_opposite_colours`, `test_winning_the_exchange_is_a_threat` |
| **TRN-UNIQ** | Never show the same position twice in one drill (one puzzle per position). | `test_a_position_blundered_twice_yields_one_puzzle`, `test_position_key_is_the_epd` |
| **TRN-LEN** | Let the user choose the drill length and get a fresh random set each drill. | `test_drill_length_caps_the_number_of_positions` |
| **TRN-TALLY** | Show a running score (total and average) across the drill. | — _not unit-tested_ |
| **TRN-SOUND** | Play short move/start sounds (best effort). | — _not unit-tested_ |

## SCT — Scout

| Requirement | Behavior | Tests |
|---|---|---|
| **SCT-FETCH** | Fetch and analyse any chess.com user as an opponent. | — _not unit-tested_ |
| **SCT-VIEW** | View that opponent's recurring mistakes via the Review analytics, to prep against them. | `test_payload_orients_and_flips_turn_for_black` |

## FLT — Filters

| Requirement | Behavior | Tests |
|---|---|---|
| **FLT-ONE** | One filter model scopes Dashboard and Review consistently. | — _not unit-tested_ |
| **FLT-DIMS** | Filter by profile, time control, colour, result, end state (winning/even/losing), how the game ended (resignation/checkmate/time/…), opening name (substring), ECO code, flagged, and analysis state. | `test_classify_end_method_normalizes_the_termination_header`, `test_eco_opening_names_picks_the_most_common_name`, `test_query_games_filters_by_colour_result_and_time_control`, `test_query_games_filters_by_end_method`, `test_query_games_filters_by_end_state`, `test_query_games_filters_by_flagged_and_analysis_state`, `test_query_games_opening_is_case_insensitive_substring` |
| **FLT-EMPTY** | Multi-value filters are multi-select, and an **empty selection means "all"** (no filter). | `test_query_games_accepts_a_list_of_values`, `test_summary_counts_accept_a_list_filter`, `test_where_in_builds_scalar_list_or_no_clause` |
| **FLT-CLOCK** | Filter to "time scrambles" — games whose remaining clock at the end was under a cutoff, choosing whose clock (mine / opponent's / either). The cutoff is an absolute figure (e.g. 5/20/60s) or a fraction of the game's base time control so one setting scales across bullet/blitz/rapid. | `test_absolute_cutoff_filters_by_whose_clock`, `test_fractional_cutoff_scales_to_the_time_control` |
| **FLT-TTL** | Filter to "time-trouble losses" — games lost to the clock: actual flags plus resignations where my clock was critically low and far behind my opponent's. | `test_lost_on_clock_needs_low_clock_and_a_lost_race`, `test_time_trouble_filter_selects_flags_and_lost_race_resigns` |
| **FLT-RECENT** | A "most recent N games" scope narrows the metrics, chart, and table together to the latest N games. | `test_recent_games_scope_cuts_off_at_nth_most_recent` |
| **FLT-COMPOS** | Active filters compose (all apply together). | `test_active_filters_apply_together` |

## MATE — MATE

| Requirement | Behavior | Tests |
|---|---|---|
| **MATE-DETECT** | For each analysed game, precompute the player's forced-mate chances: the distance (mate-in-N) when the mate first appeared, whether it was converted or blown, the key move, the forced mating line, and a motif — stored so it can be filtered and exported without re-deriving it. | `test_a_held_mate_is_one_converted_chance_at_the_starting_distance`, `test_dropping_out_of_mate_marks_the_chance_blown`, `test_non_mate_positions_start_no_chance` |
| **MATE-CONV** | Show a "mate conversion by distance" chart: for each distance (M1…MX, up to the deepest available), how often the player finished the forced mate versus blew it. | `test_conversion_by_distance_counts_finished_vs_blown` |
| **MATE-MOTIF** | Categorise each mate chance by motif (back-rank, smothered, double-check, mating piece × king location) and let the user see the breakdown and filter by it. | `test_adjacent_queen_mate_on_the_home_rank_is_not_back_rank`, `test_back_rank_needs_a_rank_check_not_just_an_edge_king`, `test_conversion_by_motif_groups_finished_vs_blown`, `test_line_that_does_not_mate_is_unknown`, `test_smothered_knight_mate` |
| **MATE-GRID** | List the mate chances in a grid the user can click to open the position on a board with the key move highlighted, showing distance, motif, converted/blown, and a link to the game. | `test_chances_grid_is_scoped_to_the_chosen_side`, `test_payload_orients_and_flips_turn_for_black` |
| **MATE-FILT** | The mate views obey the shared filters (§4.6). | `test_chances_grid_is_scoped_to_the_chosen_side` |

## NFR — Non-functional

| Requirement | Behavior | Tests |
|---|---|---|
| **NFR-LIVE** | The app is usable while analysis runs (reads see partial results). | — _not unit-tested_ |
| **NFR-FAST** | The trainer scores instantly, with no engine call at drill time. | `test_grade_cache_round_trips` |
| **NFR-CLOCK** | Trainer think-time reflects real decision time — measured while you decide, excluding the bearings pause (recorded for reference, not scored). | — _not unit-tested_ |
| **NFR-DETER** | Scoring is deterministic for a given position and elapsed time. | `test_score_attempt_is_deterministic` |
| **NFR-WIN** | Runs on Windows via uv. | — _not unit-tested_ |
