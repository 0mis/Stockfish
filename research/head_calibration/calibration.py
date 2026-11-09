"""Training-only physical-head selection and an equal-family held-out bias diagnostic.

Static and deep scores are integer centipawns in the same side-to-move frame.
The caller resolves physical head indices, declares stability, and freezes the
fit before evaluating the held-out partition. This module performs no I/O.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping
from fractions import Fraction


def _integer(value, name):
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _head(value):
    if _integer(value, "physical_head") < 0:
        raise ValueError("physical_head must be nonnegative")
    return value


def _fraction(value):
    if value is None:
        return None
    value = Fraction(value)
    return {"numerator": value.numerator, "denominator": value.denominator}


def _median(values):
    ordered = sorted(values)
    return Fraction(ordered[(len(ordered) - 1) // 2] + ordered[len(ordered) // 2], 2)


def select_heads(optimization_rows_with_physical_head):
    """Rank observed training heads by row count, breaking ties by index."""
    counts = Counter(_head(row["physical_head"]) for row in optimization_rows_with_physical_head)
    ranked = sorted(counts, key=lambda head: (-counts[head], head))
    selected = len(ranked) >= 2
    return {"status": "selected" if selected else "abstain", "optimization_rows": sum(counts.values()),
            "target_head": ranked[0] if selected else None, "control_head": ranked[1] if selected else None,
            "head_counts": [{"physical_head": head, "rows": counts[head]} for head in ranked],
            "reasons": [] if selected else ["fewer_than_two_observed_training_heads"]}


def _label_rows(labels, heads, holdout=False):
    rows, seen = [], set()
    for index, label in enumerate(labels):
        family = label["family"]
        if not isinstance(family, str) or not family:
            raise ValueError("Each label requires a family identifier")
        head = _head(label["physical_head"])
        stable = label["stable"]
        if type(stable) is not bool:
            raise ValueError("Stability must be an explicit boolean")
        position = label.get("position_id")
        if position is not None:
            if not isinstance(position, str) or not position or position in seen:
                raise ValueError("Position identifiers must be nonempty and unique within a label set")
            seen.add(position)
        row = {"label_index": index, "position_id": position, "family": family, "physical_head": head, "stable": stable}
        if stable and head in heads:
            if holdout and position is None:
                raise ValueError("Every stable held-out target row needs its position_id")
            row["parent_static_cp"] = _integer(label["parent_static_cp"], "parent_static_cp")
            row["deep_cp"] = _integer(label["deep_cp"], "deep_cp")
        rows.append(row)
    return rows


def fit_bias(labels, target_head, control_head):
    """Return one signed FC2 delta or an abstention, using training labels alone.

    A fit label may omit position_id. Its original label_index is retained.
    Stable scores are always used as deep minus parent static.
    """
    if _head(target_head) == _head(control_head):
        raise ValueError("Target and control must be different physical heads")
    rows = _label_rows(labels, {target_head, control_head})
    members = defaultdict(list)
    families = set()
    for row in rows:
        head, family = row["physical_head"], row["family"]
        if head in (target_head, control_head):
            families.add(family)
            if row["stable"]:
                members[family, head].append({**row, "residual_cp": row["deep_cp"] - row["parent_static_cp"]})

    contrasts, own_residuals, summaries = [], [], []
    paired_target_rows = paired_control_rows = 0
    for family in sorted(families):
        target = members[family, target_head]
        control = members[family, control_head]
        target_median = _median(row["residual_cp"] for row in target) if target else None
        control_median = _median(row["residual_cp"] for row in control) if control else None
        paired = target_median is not None and control_median is not None
        contrast = target_median - control_median if paired else None
        if paired:
            contrasts.append(contrast)
            own_residuals.append(target_median)
            paired_target_rows += len(target)
            paired_control_rows += len(control)
        summaries.append({"family": family, "paired": paired, "target_rows": target, "control_rows": control,
                          "target_median_residual_cp": _fraction(target_median), "control_median_residual_cp": _fraction(control_median),
                          "family_contrast_cp": _fraction(contrast)})

    paired_count = len(contrasts)
    contrast = _median(contrasts) if paired_count else None
    own = _median(own_residuals) if paired_count else None
    sign = (contrast > 0) - (contrast < 0) if contrast is not None else 0
    agreeing = sum(value * sign > 0 for value in contrasts)
    reasons = []
    if paired_count < 15:
        reasons.append("fewer_than_15_paired_training_families")
    if paired_target_rows < 50:
        reasons.append("fewer_than_50_stable_target_rows_in_paired_families")
    if paired_control_rows < 50:
        reasons.append("fewer_than_50_stable_control_rows_in_paired_families")
    if contrast is not None:
        if abs(contrast) < 6:
            reasons.append("median_family_contrast_below_6_cp")
        if abs(own) < 6:
            reasons.append("median_target_residual_below_6_cp")
        if contrast * own <= 0:
            reasons.append("contrast_and_target_residual_lack_same_nonzero_sign")
        if 3 * agreeing < 2 * paired_count:
            reasons.append("fewer_than_two_thirds_of_families_strictly_agree")
    delta = None if reasons else 512 * sign
    total_target = sum(row["physical_head"] == target_head for row in rows)
    total_control = sum(row["physical_head"] == control_head for row in rows)
    stable_target = sum(len(f["target_rows"]) for f in summaries)
    stable_control = sum(len(f["control_rows"]) for f in summaries)
    return {"status": "abstain" if reasons else "frozen", "reasons": reasons, "target_head": target_head, "control_head": control_head,
            "fc2_bias_delta": delta, "counts": {"training_rows": len(rows), "target_rows": total_target, "control_rows": total_control,
                "other_head_rows": len(rows) - total_target - total_control, "stable_target_rows": stable_target, "stable_control_rows": stable_control,
                "unstable_target_rows": total_target - stable_target, "unstable_control_rows": total_control - stable_control,
                "unpaired_stable_target_rows": stable_target - paired_target_rows, "unpaired_stable_control_rows": stable_control - paired_control_rows,
                "paired_families": paired_count, "paired_stable_target_rows": paired_target_rows, "paired_stable_control_rows": paired_control_rows,
                "positive_family_contrasts": sum(c > 0 for c in contrasts), "negative_family_contrasts": sum(c < 0 for c in contrasts),
                "zero_family_contrasts": sum(c == 0 for c in contrasts),
                "strictly_agreeing_families": agreeing},
            "median_family_contrast_cp": _fraction(contrast), "median_target_family_residual_cp": _fraction(own),
            "strict_agreement_fraction": _fraction(Fraction(agreeing, paired_count)) if paired_count else None, "families": summaries}


def _candidate_scores(evals):
    if isinstance(evals, Mapping):
        items = evals.items()
    else:
        items = ((row["position_id"], row["candidate_static_cp"]) for row in evals)
    scores = {}
    for position, value in items:
        if not isinstance(position, str) or not position or position in scores:
            raise ValueError("Candidate scores require unique nonempty position identifiers")
        scores[position] = value
    return scores


def evaluate_bias(hold_labels, candidate_evals, target_head):
    """Evaluate the frozen edit using stable held-out target labels by position_id.

    candidate_evals is an ID-to-integer-CP mapping or records with position_id
    and candidate_static_cp. Other supplied IDs do not enter the metrics.
    Missing target scores abstain. No incomplete-family mean enters the gate.
    """
    target_head = _head(target_head)
    rows = _label_rows(hold_labels, {target_head}, holdout=True)
    scores = _candidate_scores(candidate_evals)
    grouped = defaultdict(list)
    for row in rows:
        if row["stable"] and row["physical_head"] == target_head:
            grouped[row["family"]].append(row)
    required_ids = {row["position_id"] for members in grouped.values() for row in members}
    missing = sorted(position for position in required_ids if scores.get(position) is None)
    family_scores, reports = [], []
    for family in sorted(grouped):
        pairs = []
        for row in grouped[family]:
            cp = scores.get(row["position_id"])
            parent_error = abs(row["parent_static_cp"] - row["deep_cp"])
            candidate_error = None if cp is None else abs(_integer(cp, "candidate_static_cp") - row["deep_cp"])
            pairs.append({**row, "candidate_static_cp": cp, "parent_absolute_error_cp": parent_error,
                          "candidate_absolute_error_cp": candidate_error})
        parent_mean = Fraction(sum(pair["parent_absolute_error_cp"] for pair in pairs), len(pairs))
        candidate_mean = improvement = None
        if all(pair["candidate_absolute_error_cp"] is not None for pair in pairs):
            candidate_mean = Fraction(sum(pair["candidate_absolute_error_cp"] for pair in pairs), len(pairs))
            improvement = parent_mean - candidate_mean
            family_scores.append((parent_mean, candidate_mean))
        reports.append({"family": family, "rows": len(pairs), "positions": pairs, "parent_mae_cp": _fraction(parent_mean),
                        "candidate_mae_cp": _fraction(candidate_mean), "improvement_cp": _fraction(improvement)})

    reasons = []
    if len(required_ids) < 20:
        reasons.append("fewer_than_20_stable_heldout_target_rows")
    if len(grouped) < 6:
        reasons.append("fewer_than_6_stable_heldout_target_families")
    if missing:
        reasons.append("missing_heldout_target_candidate_scores")
    sufficient = not reasons
    parent_mae = candidate_mae = gain = fraction_improved = improving = tied = worsening = None
    if grouped and not missing:
        n = len(grouped)
        parent_mae = sum(a for a, _ in family_scores) / n
        candidate_mae = sum(b for _, b in family_scores) / n
        gain = parent_mae - candidate_mae
        improving = sum(a > b for a, b in family_scores)
        tied = sum(a == b for a, b in family_scores)
        worsening = n - improving - tied
        fraction_improved = Fraction(improving, n)
        if sufficient and gain < 1:
            reasons.append("mean_family_mae_improvement_below_1_cp")
        if sufficient and 5 * improving < 3 * n:
            reasons.append("fewer_than_three_fifths_of_families_strictly_improve")
    status = "abstain" if not sufficient else ("fail" if reasons else "pass")
    target_rows = sum(row["physical_head"] == target_head for row in rows)
    return {"status": status, "passed": status == "pass", "reasons": reasons, "target_head": target_head,
            "counts": {"heldout_rows": len(rows), "target_rows": target_rows, "other_head_rows": len(rows) - target_rows,
                       "unstable_target_rows": target_rows - len(required_ids),
                       "stable_target_rows": len(required_ids), "stable_target_families": len(grouped), "candidate_scores_supplied": len(scores),
                       "matched_target_scores": len(required_ids) - len(missing), "missing_target_scores": len(missing),
                       "families_with_complete_candidate_scores": len(family_scores), "strictly_improving_families": improving,
                       "tied_families": tied, "worsening_families": worsening},
            "missing_candidate_position_ids": missing, "unused_candidate_position_ids": sorted(scores.keys() - required_ids),
            "equal_family_parent_mae_cp": _fraction(parent_mae), "equal_family_candidate_mae_cp": _fraction(candidate_mae),
            "equal_family_improvement_cp": _fraction(gain), "strictly_improving_family_fraction": _fraction(fraction_improved),
            "families": reports}
