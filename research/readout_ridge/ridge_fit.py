"""Fit all 92 FC2 offsets from optimization scores and score a frozen readout.

Scores are side-to-move centipawns. The completed-depth teacher supplies the
stability flag. Every rational reported here has an exact numerator and
denominator. Inputs remain available to the caller in their original form.
"""

from collections import defaultdict
from fractions import Fraction


HEADS = 92
RIDGE = 16 * 36


def _integer(value, name):
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _ratio(value):
    return None if value is None else {"numerator": value.numerator, "denominator": value.denominator}


def _index(records):
    indexed = {}
    for row in records:
        position, family, head = row["position_id"], row["family"], _integer(row["physical_head"], "physical_head")
        if not isinstance(position, str) or not position or position in indexed:
            raise ValueError("Each input needs unique nonempty position IDs")
        if not isinstance(family, str) or not family or not 0 <= head < HEADS:
            raise ValueError(f"Invalid family or physical head at {position}")
        if "used_bucket" in row and _integer(row["used_bucket"], "used_bucket") != head:
            raise ValueError(f"Selected bucket differs from physical head at {position}")
        indexed[position] = row
    return indexed


def _join(labels, statics, score_key, *, require_complete):
    original, scores = _index(labels), _index(statics)
    unexpected = []
    for position, probe in scores.items():
        if probe["static_cp"] is not None:
            _integer(probe["static_cp"], "static_cp")
        if position not in original:
            unexpected.append({key: probe[key] for key in ("position_id", "family", "physical_head", "static_cp")})
    if require_complete and unexpected:
        raise ValueError("Probe has positions outside the optimization labels")
    rows = []
    for position, label in original.items():
        score = scores.get(position)
        if score is not None and (score["family"], score["physical_head"]) != (label["family"], label["physical_head"]):
            raise ValueError(f"Family or physical head mismatch at {position}")
        value = score["static_cp"] if score is not None else None
        if require_complete and value is None:
            raise ValueError(f"Missing optimization probe score at {position}")
        stable = label["stable"]
        if type(stable) is not bool:
            raise ValueError(f"Expected an explicit teacher stability flag at {position}")
        deep = label["deep_cp"]
        if stable or deep is not None:
            _integer(deep, "deep_cp")
        rows.append({"position_id": position, "family": label["family"], "physical_head": label["physical_head"],
                     "stable": stable, "reason": label.get("reason"), "parent_static_cp": _integer(label["parent_static_cp"], "parent_static_cp"),
                     "deep_cp": deep, score_key: value, "static_record_present": score is not None})
    return rows, unexpected


def _group(rows, key):
    result = defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return result


def _fit_head(head, rows):
    S = sum_squares = Fraction(0)
    stable_count = family_count = 0
    families = []
    for family, items in sorted(_group(rows, "family").items()):
        stable = [r for r in items if r["stable"]]
        mean_product = mean_square = None
        if stable:
            mean_product = Fraction(sum(r["response_cp"] * r["clipped_residual_cp"] for r in stable), len(stable))
            mean_square = Fraction(sum(r["response_cp"] ** 2 for r in stable), len(stable))
            S += mean_product
            sum_squares += mean_square
            stable_count += len(stable)
            family_count += 1
        families.append({"family": family, "rows": len(items), "stable_rows": len(stable),
                         "mean_sr": _ratio(mean_product), "mean_ss": _ratio(mean_square)})
    D = RIDGE + sum_squares
    steps = 32 * S / D
    reasons = []
    if stable_count < 16:
        reasons.append("fewer_than_16_stable_rows")
    if family_count < 6:
        reasons.append("fewer_than_6_stable_families")
    rounded = round(steps) if not reasons else None  # Fraction rounds exact ties to even.
    delta = max(-256, min(256, 16 * rounded)) if rounded is not None else 0
    return {"physical_head": head, "rows": len(rows), "stable_rows": stable_count, "stable_families": family_count,
            "eligible": not reasons, "support_reasons": reasons, "families": families, "S": _ratio(S), "D": _ratio(D),
            "sum_family_mean_ss": _ratio(sum_squares), "steps_before_rounding": _ratio(steps), "rounded_steps": rounded, "delta": delta}


def fit_offsets(training_labels, probe_statics):
    """Fit one 92-integer vector. The +512 probe must cover every training ID."""
    rows, _ = _join(training_labels, probe_statics, "probe_static_cp", require_complete=True)
    for row in rows:
        row["response_cp"] = row["probe_static_cp"] - row["parent_static_cp"]
        residual = row["deep_cp"] - row["parent_static_cp"] if row["stable"] else None
        row["residual_cp"] = residual
        row["clipped_residual_cp"] = max(-30, min(30, residual)) if residual is not None else None
    by_head = _group(rows, "physical_head")
    heads = [_fit_head(head, by_head[head]) for head in range(HEADS)]
    deltas = [head["delta"] for head in heads]
    return {"deltas": deltas, "identity": not any(deltas), "heads": heads, "rows": rows,
            "counts": {"training_rows": len(rows), "probe_rows": len(rows), "stable_rows": sum(r["stable"] for r in rows),
                       "unstable_rows": sum(not r["stable"] for r in rows), "eligible_heads": sum(h["eligible"] for h in heads),
                       "changed_heads": sum(d != 0 for d in deltas)}}


def _family_maes(rows):
    families = []
    parent_means, candidate_means = [], []
    for family, items in sorted(_group(rows, "family").items()):
        stable = [r for r in items if r["stable"]]
        paired = [r for r in stable if r["candidate_static_cp"] is not None]
        parent = candidate = improvement = None
        if stable:
            parent = Fraction(sum(r["parent_absolute_error_cp"] for r in stable), len(stable))
            parent_means.append(parent)
            if len(paired) == len(stable):
                candidate = Fraction(sum(r["candidate_absolute_error_cp"] for r in stable), len(stable))
                candidate_means.append(candidate)
                improvement = parent - candidate
        families.append({"family": family, "rows": len(items), "stable_rows": len(stable), "paired_stable_rows": len(paired),
                         "missing_stable_candidate_rows": len(stable) - len(paired), "parent_mae_cp": _ratio(parent),
                         "candidate_mae_cp": _ratio(candidate), "improvement_cp": _ratio(improvement)})
    parent = sum(parent_means) / len(parent_means) if parent_means else None
    candidate = sum(candidate_means) / len(candidate_means) if candidate_means and len(candidate_means) == len(parent_means) else None
    return {"families": families, "equal_family_parent_mae_cp": _ratio(parent), "equal_family_candidate_mae_cp": _ratio(candidate),
            "equal_family_improvement_cp": _ratio(parent - candidate if candidate is not None else None)}


def evaluate_offsets(holdout_labels, candidate_statics):
    """Score all stable held-out rows, giving every represented family one vote."""
    rows, unexpected = _join(holdout_labels, candidate_statics, "candidate_static_cp", require_complete=False)
    for row in rows:
        row["parent_absolute_error_cp"] = abs(row["parent_static_cp"] - row["deep_cp"]) if row["stable"] else None
        row["candidate_absolute_error_cp"] = (abs(row["candidate_static_cp"] - row["deep_cp"])
                                              if row["stable"] and row["candidate_static_cp"] is not None else None)
    by_head = _group(rows, "physical_head")
    heads = []
    for head in range(HEADS):
        items = by_head[head]
        heads.append({"physical_head": head, "rows": len(items), "stable_rows": sum(r["stable"] for r in items),
                      "paired_stable_rows": sum(r["candidate_absolute_error_cp"] is not None for r in items), **_family_maes(items)})
    missing = [r["position_id"] for r in rows if r["candidate_static_cp"] is None]
    missing_stable = [r["position_id"] for r in rows if r["stable"] and r["candidate_static_cp"] is None]
    stable_count = sum(r["stable"] for r in rows)
    return {**_family_maes(rows), "heads": heads, "rows": rows, "missing_candidate_position_ids": missing,
            "missing_stable_candidate_position_ids": missing_stable, "unexpected_candidate_records": unexpected,
            "counts": {"heldout_rows": len(rows), "stable_rows": stable_count, "unstable_rows": len(rows) - stable_count,
                       "stable_families": len({r["family"] for r in rows if r["stable"]}),
                       "paired_stable_rows": stable_count - len(missing_stable), "missing_candidate_rows": len(missing),
                       "missing_stable_candidate_rows": len(missing_stable), "unexpected_candidate_rows": len(unexpected)}}
