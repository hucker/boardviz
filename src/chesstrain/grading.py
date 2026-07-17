"""Trainer scoring: eval-grade + time penalty, and the win/loss readout.

The eval grade (+2/+1/-1/-2) comes from the precomputed ``grades_cache``; this
module adds the *time* dimension the user asked for (blitz -1 after 10s, -2
after 20s) and combines them into a final clamped score.
"""

from __future__ import annotations

from . import config


def time_penalty(elapsed_s: float, tc_class: str) -> int:
    """Penalty in {0, -1, -2} for taking `elapsed_s` in a `tc_class` position.

    Uses the per-class curve in ``config.TIME_PENALTY_CURVES``; the most severe
    threshold at or below the elapsed time wins.
    """
    curve = config.TIME_PENALTY_CURVES.get(tc_class, [])
    penalty = 0
    for threshold, pen in curve:
        if elapsed_s >= threshold:
            penalty = pen
    return penalty


def score_attempt(grades: dict[str, int], uci: str, elapsed_s: float,
                  tc_class: str) -> dict:
    """Grade a trainer move and fold in the time penalty.

    Args:
        grades: EPD's move->grade map (unknown/illegal move => -2).
        uci: the move played.
        elapsed_s: seconds the user took.
        tc_class: time-control class driving the penalty curve.

    Returns:
        {grade, time_penalty, final_score} with final clamped to [-2, +2].
    """
    grade = grades.get(uci, -2)
    penalty = time_penalty(elapsed_s, tc_class)
    final = max(-2, min(2, grade + penalty))
    return {"grade": grade, "time_penalty": penalty, "final_score": final}


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
