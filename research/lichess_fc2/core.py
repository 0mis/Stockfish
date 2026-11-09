"""Exact sparse FC2 calibration. Pure functions; no file, engine or network I/O.

The connected component is the family weighting unit. Published teacher bounds
and engine versions remain source limitations. SPDX-License-Identifier: GPL-3.0-or-later
"""
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json


PLAN_SHA256 = "342731327ecba62106e917591012af95c8c48eb315e3f6e01054ea331102e388"
PARENT_SHA256 = "18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf"
PARENT_BYTES = 138360528
DELTAS = (-4, -2, -1, 0, 1, 2, 4)
QUANTIZER = {"numerator": 9600, "first_divisor": 16384, "second_divisor": 16,
             "rounding": "toward_zero", "intermediate_bits": 32}


class ContractError(ValueError):
    pass


class Unavailable(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise ContractError(message)


def integer(value):
    return type(value) is int


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def fraction(value):
    value = Fraction(value)
    return {"numerator": value.numerator, "denominator": value.denominator}


def hash_string(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def i32(value):
    return integer(value) and -(1 << 31) <= value < (1 << 31)


def trunc0(numerator, denominator):
    require(integer(numerator) and integer(denominator) and denominator > 0, "Expected exact signed division")
    return numerator // denominator if numerator >= 0 else -((-numerator) // denominator)


def raw_from_combined(combined):
    if not i32(combined) or not i32(combined * 9600):
        raise Unavailable("unsafe_int32_quantizer_intermediate")
    return trunc0(trunc0(combined * 9600, 16384), 16)


def validate_mapping(mapping, *, allow_synthetic=False):
    require(mapping["schema"] == "test1757-fc2-byte-map/1" and mapping["verified"] is True, "An independently verified byte map is required")
    require(hash_string(mapping["parent_sha256"]) and integer(mapping["parent_bytes"]) and mapping["parent_bytes"] > 0, "Invalid parent binding")
    if not allow_synthetic:
        require(mapping["parent_sha256"] == PARENT_SHA256 and mapping["parent_bytes"] == PARENT_BYTES, "Wrong original test1757 parent")
    require(mapping["stored_heads"] == len(mapping["heads"]) == 92 and mapping["head_width"] == 128, "Wrong test1757 FC2 geometry")
    require(mapping["quantizer"] == QUANTIZER, "Unsupported upstream quantizer contract")
    routes = mapping["raw_to_physical"]
    require(len(routes) == 192 and all(integer(h) and 0 <= h < 92 for h in routes), "Invalid raw routing table")
    previous_end = 0
    for index, head in enumerate(mapping["heads"]):
        require(integer(head["physical_head"]) and head["physical_head"] == index, "Physical heads must be ordered0..91")
        offset = head["bias_offset"]
        require(integer(offset) and previous_end <= offset <= mapping["parent_bytes"] - 132 and i32(head["bias_before"]), "Invalid FC2 bias record")
        require(head["weight_offsets"] == list(range(offset + 4, offset + 132)), "FC2 serialized lanes must follow their declared bias")
        require(len(head["weights"]) == 128 and all(integer(w) and -128 <= w <= 127 for w in head["weights"]), "Invalid signed int8 coefficients")
        require(len(head["serialized_to_native"]) == 128 and sorted(head["serialized_to_native"]) == list(range(128))
                and all(integer(i) for i in head["serialized_to_native"]), "Invalid serialized/native permutation")
        previous_end = offset + 132
    return mapping


def join_teacher_rows(bundle, captures_by_position_id):
    """Join a caller-supplied partition and child captures; this never reads files.

    The earlier data-only native_selection_allowed flag is provenance, not an
    additional permission or global opening-book closure gate.
    """
    require(bundle["schema"] == "lichess-public-multipv-teachers-v1", "Wrong teacher schema")
    output = []
    for source in bundle["rows"]:
        require([c["rank"] for c in source["candidates"]] == [1, 2], "Published candidate order must be preserved")
        require(source["root_pov"] in ("white", "black"), "Published root POV is required")
        for candidate in source["candidates"]:
            require(candidate["source_mate"] is None and integer(candidate["source_white_cp"]) and integer(candidate["root_cp"]), "A published CP pair is required")
            require(candidate["root_cp"] == candidate["source_white_cp"] * (1 if source["root_pov"] == "white" else -1), "Published White/root score conversion differs")
        require(source["teacher_margin_cp"] == source["candidates"][0]["root_cp"] - source["candidates"][1]["root_cp"], "Published A/B margin differs")
        children = []
        for candidate in source["candidates"]:
            child = candidate["child"]
            require(candidate["standard_uci"], "A published first move is required")
            key = (source["position_id"], candidate["rank"])
            capture = captures_by_position_id[key] if key in captures_by_position_id else captures_by_position_id.get(child["position_id"])
            children.append({"rank": candidate["rank"], "position_id": child["position_id"],
                             "move_uci": candidate["standard_uci"][0], "in_check": child["in_check"], "terminal": child["terminal"],
                             "expected_rule50": int(child["fen"].split()[4]) if "fen" in child else None, "capture": capture})
        output.append({key: source[key] for key in ("position_id", "component_id", "component_split_sha256", "split", "root_pov", "teacher_margin_cp")}
                      | {"children": children})
    return output


def _metadata(rows, expected_split, split_sha256):
    require(hash_string(split_sha256), "A predeclared split digest is required")
    require(all(row.get("split") == expected_split for row in rows), "Only the requested partition may be supplied")
    ids = set()
    for row in rows:
        require(row["component_split_sha256"] == split_sha256 and isinstance(row["component_id"], str) and row["component_id"], "Component split binding differs")
        require(isinstance(row["position_id"], str) and row["position_id"] and row["position_id"] not in ids, "Repeated or missing paired root")
        require(row["root_pov"] in ("white", "black") and integer(row["teacher_margin_cp"]) and row["teacher_margin_cp"] >= 0, "Invalid published root-perspective margin")
        require(len(row["children"]) == 2 and [c["rank"] for c in row["children"]] == [1, 2], "A/B child order differs")
        ids.add(row["position_id"])


def _prepare_child(child, root, mapping):
    require(type(child["in_check"]) is bool and type(child["terminal"]) is bool, "Verified child board dispositions are required")
    # Counterfactual raw/LUT values do not make ordinary eval available in check.
    if child["terminal"]:
        raise Unavailable("terminal_child_ordinary_static_unavailable")
    if child["in_check"]:
        raise Unavailable("in_check_child_ordinary_static_unavailable")
    cap = child.get("capture")
    if cap is None:
        raise Unavailable("missing_child_capture")
    require(cap["schema"] == "test1757-fc2-activation/1", "Wrong activation schema")
    for key in ("network_sha256", "parent_sha256"):
        if key in cap:
            require(cap[key] == mapping["parent_sha256"], "Capture parent network binding differs")
    if "byte_map_sha256" in cap:
        require(cap["byte_map_sha256"] == digest(mapping), "Capture byte-map binding differs")
    for key, value in (("parent_position_id", root["position_id"]), ("rank", child["rank"]),
                       ("child_position_id", child["position_id"]), ("position_id", child["position_id"]),
                       ("component_id", root["component_id"]), ("component_split_sha256", root["component_split_sha256"]), ("split", root["split"])):
        if key in cap:
            require(cap[key] == value, "Capture request/component identity differs: " + key)
    if child.get("expected_rule50") is not None:
        require(cap["rule50"] == child["expected_rule50"], "Actual child rule50 differs from the published child")
    if cap.get("ordinary_static_available", True) is not True or cap.get("static_loss_available", True) is not True:
        raise Unavailable("ordinary_static_unavailable:" + str(cap.get("unavailable_reason", "unspecified")))
    if cap.get("in_check") is True or cap.get("terminal") is True:
        raise Unavailable("capture_board_ordinary_static_unavailable")
    head_index = cap["physical_head"]
    require(integer(head_index) and 0 <= head_index < 92 and cap["stored_heads"] == 92, "Invalid captured physical head")
    head = mapping["heads"][head_index]
    require(integer(cap["side_to_move"]) and cap["side_to_move"] == (1 if root["root_pov"] == "white" else 0), "Child side is not opposite the root player")
    for key in ("fc0", "fc1"):
        require(len(cap[key]) == 32 and all(i32(v) for v in cap[key]), "Invalid captured preactivation vector")
    native, serialized = cap["input_native"], cap["input_serialized"]
    require(len(native) == len(serialized) == 128 and all(integer(v) and 0 <= v <= 127 for v in native + serialized), "Invalid canonical FC2 activations")
    require(cap["serialized_to_native"] == head["serialized_to_native"]
            and serialized == [native[i] for i in head["serialized_to_native"]], "Canonical serialized activation order differs")
    require(cap["output_weights"] == head["weights"] and cap["output_bias"] == head["bias_before"], "Captured FC2 coefficients/bias differ from exact parent")
    affine = head["bias_before"] + sum(w * x for w, x in zip(head["weights"], serialized))
    require(i32(affine) and cap["fc2_affine"] == affine and cap["skip"] == cap["fc0"][30] - cap["fc0"][31]
            and cap["combined"] == affine + cap["skip"], "Original affine/skip accumulator differs")
    try:
        raw = raw_from_combined(cap["combined"])
    except Unavailable as exc:
        raise Unavailable("original_" + str(exc)) from exc
    require(cap["scaled"] == trunc0(cap["combined"] * 9600, 16384)
            and cap["raw_nnue"] == cap["ordinary_raw_nnue"] == raw, "Original quantization/native parity differs")
    lo, hi = cap["raw_offset_min"], cap["raw_offset_max"]
    require(integer(lo) and integer(hi) and lo <= 0 <= hi, "Invalid raw-offset window")
    cp, zero = cap["raw_offset_cp"], cap["raw_offset_static_zero"]
    require(len(cp) == len(zero) == hi - lo + 1
            and all(value is None or integer(value) for value in cp + zero), "Invalid engine CP lookup")
    if cp[-lo] is None or zero[-lo] is None:
        raise Unavailable("missing_zero_offset_lookup")
    require(integer(cap["ordinary_static_cp"]) and integer(cap["ordinary_static_zero"])
            and cp[-lo] == cap["ordinary_static_cp"] and zero[-lo] == cap["ordinary_static_zero"], "Ordinary CP zero-offset parity differs")
    return {"head": head_index, "input": tuple(serialized), "combined": cap["combined"], "raw": raw,
            "lo": lo, "hi": hi, "cp": tuple(cp), "zero": tuple(zero)}


def _prepare_pair(row, mapping):
    children, reasons = [], []
    for child in row["children"]:
        try:
            children.append(_prepare_child(child, row, mapping))
        except Unavailable as exc:
            children.append(None)
            reasons.append(f"child{child['rank']}:{exc}")
    return {"row": row, "children": children, "reasons": reasons}


def child_cp(child, edit=None):
    raw_delta = 0
    if edit is not None:
        combined = child["combined"] + edit["delta"] * child["input"][edit["lane"]]
        raw_delta = raw_from_combined(combined) - child["raw"]
    if not child["lo"] <= raw_delta <= child["hi"]:
        raise Unavailable("raw_delta_outside_engine_lookup")
    index = raw_delta - child["lo"]
    if child["cp"][index] is None or child["zero"][index] is None:
        raise Unavailable("missing_engine_lookup_entry")
    return child["cp"][index]


def root_margin(child_a_stm_cp, child_b_stm_cp):
    require(integer(child_a_stm_cp) and integer(child_b_stm_cp), "Root margins require exact child STM CP")
    return -child_a_stm_cp + child_b_stm_cp


def huber20(deficit):
    deficit = Fraction(deficit)
    require(deficit >= 0, "Huber deficit must be nonnegative")
    return deficit * deficit / 2 if deficit <= 20 else 20 * (deficit - 10)


def _loss(margin, teacher_margin):
    deficit = max(0, min(60, teacher_margin) - margin)
    return huber20(deficit), deficit


def component_mean(values):
    """values is an iterable of(component_id,exact_value); each component is equal."""
    groups = defaultdict(list)
    for group, value in values:
        groups[group].append(Fraction(value))
    return sum((sum(items, Fraction()) / len(items) for items in groups.values()), Fraction()) / len(groups) if groups else None


def candidate_order_key(cost, delta, lane):
    return Fraction(cost), 0 if delta == 0 else 1, abs(delta), -1 if lane is None else lane, DELTAS.index(delta)


def _support(rows, mapping):
    groups, dispositions = defaultdict(list), []
    for row in rows:
        prepared = _prepare_pair(row, mapping)
        reasons = list(prepared["reasons"])
        if not reasons and prepared["children"][0]["head"] != prepared["children"][1]["head"]:
            reasons.append("cross_head_training_pair")
        if not reasons:
            groups[prepared["children"][0]["head"]].append(prepared)
        dispositions.append({"position_id": row["position_id"], "status": "eligible" if not reasons else "unavailable", "reasons": reasons})
    support = [{"physical_head": h, "paired_roots": len(items), "components": len({p["row"]["component_id"] for p in items}),
                "eligible": len(items) >= 24 and len({p["row"]["component_id"] for p in items}) >= 8} for h, items in sorted(groups.items())]
    selected = sorted((s for s in support if s["eligible"]), key=lambda s: (-s["paired_roots"], s["physical_head"]))[:4]
    return groups, support, selected, dispositions


def support_summary(rows, mapping, split_sha256, *, allow_synthetic=False):
    _metadata(rows, "train", split_sha256)
    validate_mapping(mapping, allow_synthetic=allow_synthetic)
    _, support, selected, dispositions = _support(rows, mapping)
    return {"support": support, "selected": selected, "rows": dispositions}


def _candidate_loss(items, edit):
    losses = []
    for prepared in items:
        a, b = prepared["children"]
        margin = root_margin(child_cp(a, edit), child_cp(b, edit))
        loss, _ = _loss(margin, prepared["row"]["teacher_margin_cp"])
        losses.append((prepared["row"]["component_id"], loss))
    return component_mean(losses)


def fit_training(rows, mapping, split_sha256, *, provenance=None, allow_synthetic=False):
    """Fit only the supplied training partition; return one sealed joint freeze."""
    _metadata(rows, "train", split_sha256)
    validate_mapping(mapping, allow_synthetic=allow_synthetic)
    groups, support, selected, dispositions = _support(rows, mapping)
    choices, edits, candidates = [], [], []
    for summary in selected:
        head_index = summary["physical_head"]
        head, items = mapping["heads"][head_index], groups[head_index]
        baseline = _candidate_loss(items, None)
        best = {"physical_head": head_index, "lane": None, "delta": 0, "loss": baseline, "cost": baseline}
        audit = [{"lane": None, "delta": 0, "status": "available", "loss": fraction(baseline), "cost": fraction(baseline)}]
        for lane in range(128):
            for delta in DELTAS:
                if delta == 0:
                    continue
                candidate = {"physical_head": head_index, "lane": lane, "delta": delta}
                if not -128 <= head["weights"][lane] + delta <= 127:
                    audit.append({"lane": lane, "delta": delta, "status": "unavailable", "reason": "int8_coefficient_bound"})
                    continue
                try:
                    loss = _candidate_loss(items, candidate)
                except Unavailable as exc:
                    audit.append({"lane": lane, "delta": delta, "status": "unavailable", "reason": str(exc)})
                    continue
                cost = loss + Fraction(delta * delta, 8)
                audit.append({"lane": lane, "delta": delta, "status": "available", "loss": fraction(loss), "cost": fraction(cost)})
                if candidate_order_key(cost, delta, lane) < candidate_order_key(best["cost"], best["delta"], best["lane"]):
                    best = {**candidate, "loss": loss, "cost": cost}
        choice = {**summary, "lane": best["lane"], "delta": best["delta"], "baseline_loss": fraction(baseline),
                  "loss": fraction(best["loss"]), "regularization": fraction(Fraction(best["delta"] ** 2, 8)), "cost": fraction(best["cost"])}
        choices.append(choice)
        candidates.append({"physical_head": head_index, "fixed_paired_roots": len(items), "candidates": audit})
        if best["delta"]:
            lane = best["lane"]
            edits.append({"physical_head": head_index, "lane": lane, "delta": best["delta"], "offset": head["weight_offsets"][lane],
                          "before": head["weights"][lane], "after": head["weights"][lane] + best["delta"]})
    frozen = {"schema": "lichess-fc2-joint-freeze/1", "status": "joint_frozen_before_validation", "plan_sha256": PLAN_SHA256,
              "parent_sha256": mapping["parent_sha256"], "parent_bytes": mapping["parent_bytes"], "mapping_sha256": digest(mapping),
              "component_split_sha256": split_sha256, "training_rows_sha256": digest(rows), "training_root_ids": sorted(row["position_id"] for row in rows),
              "training_components": sorted({row["component_id"] for row in rows}), "support": support, "row_dispositions": dispositions,
              "training_coverage": {"supplied_roots": len(rows), "same_head_measurable_roots": sum(d["status"] == "eligible" for d in dispositions),
                                    "unavailable_for_fitting_roots": sum(d["status"] == "unavailable" for d in dispositions),
                                    "exclusions": dict(sorted(Counter(reason for d in dispositions for reason in d["reasons"]).items()))},
              "choices": choices, "edits": sorted(edits, key=lambda e: e["physical_head"]), "candidate_audit": candidates,
              "identity": not edits, "provenance": provenance or {}, "validation_opened": False,
              "iterative_reselection": False, "method": "supervised_sparse_calibration", "verified_gain": False}
    frozen["freeze_sha256"] = digest(frozen)
    return frozen


def verify_freeze(frozen, mapping):
    require(isinstance(frozen, dict) and frozen.get("schema") == "lichess-fc2-joint-freeze/1"
            and frozen.get("status") == "joint_frozen_before_validation", "A joint frozen recipe is required before validation")
    require(frozen["freeze_sha256"] == digest({k: v for k, v in frozen.items() if k != "freeze_sha256"}), "Joint freeze digest differs")
    require(frozen["plan_sha256"] == PLAN_SHA256 and frozen["mapping_sha256"] == digest(mapping)
            and frozen["parent_sha256"] == mapping["parent_sha256"] and frozen["parent_bytes"] == mapping["parent_bytes"], "Freeze source binding differs")
    require(frozen["validation_opened"] is False and frozen["iterative_reselection"] is False, "Validation/reselection was opened before the freeze")
    require(len(frozen["choices"]) <= 4 and len({c["physical_head"] for c in frozen["choices"]}) == len(frozen["choices"]), "Too many or repeated chosen heads")
    expected = []
    for choice in frozen["choices"]:
        require(integer(choice["delta"]) and choice["delta"] in DELTAS and choice["paired_roots"] >= 24 and choice["components"] >= 8, "Frozen support/delta differs")
        require(integer(choice["physical_head"]) and 0 <= choice["physical_head"] < 92, "Frozen physical head differs")
        if choice["delta"] == 0:
            require(choice["lane"] is None, "Identity must have no lane")
            continue
        h, lane, delta = choice["physical_head"], choice["lane"], choice["delta"]
        require(integer(h) and 0 <= h < 92 and integer(lane) and 0 <= lane < 128, "Frozen lane/head differs")
        head = mapping["heads"][h]
        before, after = head["weights"][lane], head["weights"][lane] + delta
        require(-128 <= after <= 127, "Frozen edit violates int8 bounds")
        expected.append({"physical_head": h, "lane": lane, "delta": delta, "offset": head["weight_offsets"][lane], "before": before, "after": after})
    require(frozen["edits"] == sorted(expected, key=lambda e: e["physical_head"]) and frozen["identity"] is (not expected), "Frozen edit bytes differ")
    return frozen


def score_validation(rows, mapping, frozen, bound_patch, *, allow_synthetic=False):
    """Score all validation dispositions once; never select or mutate edits."""
    validate_mapping(mapping, allow_synthetic=allow_synthetic)
    verify_freeze(frozen, mapping)
    require(isinstance(bound_patch, dict) and bound_patch.get("schema") == "test1757-fc2-byte-patch/1"
            and bound_patch.get("patch_sha256") == digest({k: v for k, v in bound_patch.items() if k != "patch_sha256"})
            and bound_patch["freeze_sha256"] == frozen["freeze_sha256"] and bound_patch["mapping_sha256"] == digest(mapping)
            and bound_patch["parent_sha256"] == mapping["parent_sha256"] and hash_string(bound_patch["candidate_sha256"])
            and bound_patch["bytes"] == mapping["parent_bytes"] and bound_patch["inverse_sha256"] == mapping["parent_sha256"]
            and bound_patch["biases_unchanged"] is True and bound_patch["changed_bytes"] == len(frozen["edits"])
            and bound_patch["identity"] is frozen["identity"] and bound_patch["edits"] == frozen["edits"], "A byte-bound joint freeze is required before validation")
    _metadata(rows, "validation", frozen["component_split_sha256"])
    require(not set(row["position_id"] for row in rows).intersection(frozen["training_root_ids"])
            and not set(row["component_id"] for row in rows).intersection(frozen["training_components"]), "Validation intersects training components/roots")
    edits = {edit["physical_head"]: edit for edit in frozen["edits"]}
    reports, baseline_all, matched = [], [], []
    for row in rows:
        prepared = _prepare_pair(row, mapping)
        reasons = list(prepared["reasons"])
        entry = {"position_id": row["position_id"], "component_id": row["component_id"], "target_cp": min(60, row["teacher_margin_cp"]), "reasons": reasons}
        if reasons:
            entry.update(status="unavailable", baseline_margin_cp=None, candidate_margin_cp=None)
        else:
            a, b = prepared["children"]
            baseline = root_margin(child_cp(a), child_cp(b))
            base_loss, base_deficit = _loss(baseline, row["teacher_margin_cp"])
            baseline_all.append((row["component_id"], base_loss))
            entry.update(baseline_margin_cp=baseline, baseline_deficit_cp=base_deficit, baseline_loss=fraction(base_loss),
                         physical_heads=[a["head"], b["head"]], touches_changed_head=bool({a["head"], b["head"]}.intersection(edits)),
                         cross_head_pair=a["head"] != b["head"])
            try:
                margin = root_margin(child_cp(a, edits.get(a["head"])), child_cp(b, edits.get(b["head"])))
                loss, deficit = _loss(margin, row["teacher_margin_cp"])
                entry.update(status="measured", candidate_margin_cp=margin, candidate_deficit_cp=deficit, candidate_loss=fraction(loss))
                matched.append((row["component_id"], base_loss, loss, baseline, margin, base_deficit, deficit, entry["touches_changed_head"]))
            except Unavailable as exc:
                entry.update(status="unavailable", candidate_margin_cp=None)
                reasons.append("candidate:" + str(exc))
        reports.append(entry)

    def metric(index, items):
        value = component_mean((item[0], item[index]) for item in items)
        return fraction(value) if value is not None else None

    metrics = {"matched_rows": len(matched), "matched_components": len({item[0] for item in matched}),
               "baseline_loss": metric(1, matched), "candidate_loss": metric(2, matched),
               "baseline_margin_cp": metric(3, matched), "candidate_margin_cp": metric(4, matched),
               "baseline_deficit_cp": metric(5, matched), "candidate_deficit_cp": metric(6, matched)}
    available_baseline = component_mean(baseline_all)
    return {"schema": "lichess-fc2-validation-proxy/1", "freeze_sha256": frozen["freeze_sha256"], "patch_sha256": bound_patch["patch_sha256"],
            "validation_rows_sha256": digest(rows), "rows": reports, "metrics_on_matched_rows": metrics,
            "baseline_all_measurable_loss": fraction(available_baseline) if available_baseline is not None else None,
            "coverage": {"supplied_rows": len(rows), "baseline_measurable_rows": len(baseline_all), "matched_rows": len(matched),
                         "unavailable_rows": sum(row["status"] == "unavailable" for row in reports),
                         "matched_touching_changed_head": sum(item[7] for item in matched), "matched_unchanged_heads": sum(not item[7] for item in matched),
                         "matched_cross_head_pairs": sum(row.get("cross_head_pair", False) for row in reports if row["status"] == "measured"),
                         "exclusions": dict(sorted(Counter(reason for row in reports for reason in row["reasons"]).items()))},
            "proxy_metrics_only": True, "selection_reopened": False, "verified_gain": False}
