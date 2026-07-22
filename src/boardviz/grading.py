"""Trainer scoring: eval-grade + time penalty, and the win/loss readout.

The eval grade (+2/+1/-1/-2) comes from the precomputed ``grades_cache``; this
module adds the *time* dimension the user asked for (blitz -1 after 10s, -2
after 20s) and combines them into a final clamped score.
"""

from __future__ import annotations

from . import config


def score_attempt(grades: dict[str, int], uci: str) -> dict:
    """Score a trainer move by quality alone — time is not counted.

    +1 for a good move (best or a sound alternative, grade ≥ 1), +0.5 for an
    inaccuracy (grade −1), 0 for a blunder (grade −2, or an unknown/illegal
    move) — so the total reads as points out of the positions drilled. Returns
    {grade, final_score}.
    """
    grade = grades.get(uci, -2)
    final = 1.0 if grade >= 1 else 0.5 if grade == -1 else 0.0
    return {"grade": grade, "final_score": final}


def win_loss_readout(eval_cp: int, threshold_cp: int = config.WIN_THRESHOLD_CP,
                     pov: str = "you") -> str:
    """Human phrase for whether the mover is winning/losing by more than X.

    ``eval_cp`` is mover-POV centipawns. Returns e.g. "You are winning by more
    than 2.0 (+3.4)" or "Roughly equal (+0.3)".
    """
    pawns = eval_cp / 100.0
    thr = threshold_cp / 100.0
    sign = f"{pawns:+.1f}"
    subj = pov.capitalize()
    aux = "are" if pov.lower() == "you" else "is"
    if eval_cp >= threshold_cp:
        return f"{subj} {aux} winning by more than {thr:.1f} ({sign})"
    if eval_cp <= -threshold_cp:
        return f"{subj} {aux} losing by more than {thr:.1f} ({sign})"
    return f"Roughly equal ({sign})"
