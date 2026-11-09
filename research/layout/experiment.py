"""Fit one channel permutation on public games and check the frozen holdout."""
import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import chess
import numpy as np

import corpus
import features_v19
import optimize_layout
import permute_nnue


PLAN_SHA = "d611f1f567ba2359d69a388aac98b5004565ce4fda2b5f7ef570226ba2177f53"
FEATURES_SHA = "b494c4e11dafdfc9598c3c591c0dca720f41498ae5944d8dc384f03737a58e6e"
HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def publish_json(path, value):
    with path.open("xb") as stream:
        stream.write(json_bytes(value))


def activations(rows, tables):
    values = features_v19.transform_many((row["fen"] for row in rows), tables)
    require(values.shape == (len(rows), 1024) and values.dtype == np.uint8, "Unexpected FT shape or type")
    require(int(values.max()) <= 127, "FT quantization outside uint8 product range")
    return values


def save_masks(path, rows, values):
    masks = values == 0
    packed = np.packbits(masks, axis=1, bitorder="little")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row, activation, bits in zip(rows, values, packed, strict=True):
            line = {"position_id": row["position_id"], "family": row["family"],
                    "activation_sha256": sha(activation.tobytes()), "zero_bits_little_hex": bits.tobytes().hex()}
            stream.write(json.dumps(line, sort_keys=True) + "\n")
    return masks


def gate(permutation, holdout, masks):
    counts = optimize_layout.evaluate_layout(permutation, masks)
    by_family = defaultdict(list)
    for index, row in enumerate(holdout):
        by_family[row["family"]].append(index)
    require(len(by_family) == 15, "Incomplete holdout family set")
    families = {}
    for family, indices in sorted(by_family.items()):
        families[family] = optimize_layout.evaluate_layout(permutation, masks[indices])
    favorable = sum(item["permuted_zero_groups"] > item["identity_zero_groups"] for item in families.values())
    original_active = counts["total_groups"] - counts["identity_zero_groups"]
    reduction = counts["permuted_zero_groups"] - counts["identity_zero_groups"]
    passed = original_active > 0 and 100 * reduction >= original_active and favorable >= 8
    return {"counts": counts, "families": families, "favorable_families": favorable,
            "identity_active_groups": original_active, "active_groups_saved": reduction,
            "relative_active_groups_saved": reduction / original_active if original_active else None,
            "passed": passed}


def native_rows(holdout):
    selected = defaultdict(list)
    for row in holdout:
        if len(selected[row["family"]]) < 2:
            selected[row["family"]].append(row)
    return [row for family in sorted(selected) for row in selected[family]]


def verify_activation_order(rows, parent_activations, candidate_tables, permutation):
    order = np.asarray(permutation + [index + 512 for index in permutation], dtype=np.intp)
    actual = activations(rows, candidate_tables)
    require(np.array_equal(actual, parent_activations[:, order]), "Edited activations do not follow the declared order")


def run(inputs, output):
    inputs, output = inputs.resolve(strict=True), output.resolve()
    plan_bytes = (HERE / "plan.json").read_bytes()
    require(sha(plan_bytes) == PLAN_SHA, "Prospective layout plan changed")
    require(sha((HERE / "features_v19.py").read_bytes()) == FEATURES_SHA, "Feature extraction source changed")
    plan = json.loads(plan_bytes)
    publish_json(output / "PLAN.json", plan)
    source = {"driver": os.environ.get("GITHUB_SHA"), "experiment": plan["experiment"], "plan_sha256": PLAN_SHA,
              "features_sha256": FEATURES_SHA, "original_network_sha256": permute_nnue.PARENT_SHA256,
              "python": platform.python_version(), "numpy": np.__version__, "chess": chess.__version__}
    publish_json(output / "SOURCE.json", source)
    schedule = corpus.build_corpus(inputs / "edited.pgn", inputs / "parent.pgn")
    publish_json(output / "CORPUS.json", schedule)
    result = {"source": source, "heldout_gate_passed": False, "verified_elo_gain": False,
              "corpus_sha256": sha((output / "CORPUS.json").read_bytes())}
    if schedule["status"] != "ready":
        result.update(status="corpus_infeasible", issues=schedule["issues"])
        publish_json(output / "RESULT.json", result)
        return result

    # Read the exact serialized weights once. These array views stay in canonical order.
    net = inputs / "parent.nnue"
    original = net.read_bytes()
    layout = permute_nnue.verify_parent(original)
    tables = permute_nnue.ft_views(original, layout)
    fit_rows, heldout = schedule["optimization"], schedule["holdout"]
    fit_values = activations(fit_rows, tables)
    fit_masks = save_masks(output / "optimization-masks.jsonl", fit_rows, fit_values)
    fitted = optimize_layout.fit_layout(fit_masks)
    permutation = fitted["permutation"]
    publish_json(output / "FIT.json", fitted)
    perm_path = output / "PERMUTATION.json"
    publish_json(perm_path, permutation)
    permutation_sha = sha(perm_path.read_bytes())
    result["permutation_sha256"] = permutation_sha

    # The holdout values are computed only after the fitted permutation is fixed.
    hold_values = activations(heldout, tables)
    hold_masks = save_masks(output / "holdout-masks.jsonl", heldout, hold_values)
    require(sha(perm_path.read_bytes()) == permutation_sha and json.loads(perm_path.read_bytes()) == permutation,
            "Fitted order changed before holdout scoring")
    hold_result = gate(permutation, heldout, hold_masks)
    publish_json(output / "HOLDOUT.json", hold_result)
    result.update(optimization_positions=len(fit_rows), holdout_positions=len(heldout),
                  holdout_favorable_families=hold_result["favorable_families"],
                  holdout_relative_active_groups_saved=hold_result["relative_active_groups_saved"])
    if not hold_result["passed"]:
        result["status"] = "ended_at_holdout_gate"
        publish_json(output / "RESULT.json", result)
        return result

    # Keep the candidate outside the artifact until the inverse, layer and native checks pass.
    from native_check import check_native
    with tempfile.TemporaryDirectory(prefix="layout-work-", dir=output.parent) as temp:
        work = Path(temp)
        edited_path = work / "candidate.nnue"
        edit = permute_nnue.write_candidate(net, permutation, edited_path, output / "EDIT.json")
        require(edit["inverse_sha256"] == permute_nnue.PARENT_SHA256, "Lossless inverse failed")
        edited = edited_path.read_bytes()
        require(sha(edited) == edit["candidate_sha256"], "Candidate payload changed")
        candidate_layout = permute_nnue.parse_layout(edited)
        require(layout == candidate_layout, "Candidate route/format differs")
        candidate_tables = permute_nnue.ft_views(edited, candidate_layout)
        verify_activation_order(fit_rows, fit_values, candidate_tables, permutation)
        verify_activation_order(heldout, hold_values, candidate_tables, permutation)
        probes = native_rows(heldout)
        check = check_native(inputs / "reference.exe", net, edited_path, probes, output, work)
        native_receipt = (output / "native-check.json").read_bytes()
        require(check.get("equality_verified") is True and json.loads(native_receipt) == check,
                "Native equality receipt differs from the completed check")
        require(sha(edited_path.read_bytes()) == edit["candidate_sha256"], "Candidate changed during native checks")
        require(sha(net.read_bytes()) == permute_nnue.PARENT_SHA256, "Parent changed during native checks")
        edited_path.rename(output / "candidate.nnue")
    result.update(status="candidate_ready_for_timing", heldout_gate_passed=True,
                  candidate_sha256=edit["candidate_sha256"], inverse_sha256=edit["inverse_sha256"],
                  activation_rows_exactly_permuted=len(fit_rows) + len(heldout), native_positions=len(probes),
                  native_check_sha256=sha(native_receipt))
    publish_json(output / "RESULT.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    try:
        result = run(args.inputs, args.output)
    except Exception as exc:
        failure = args.output / "FAILED.json"
        if args.output.is_dir() and not failure.exists():
            publish_json(failure, {"error_type": type(exc).__name__, "message": str(exc)})
        raise
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
