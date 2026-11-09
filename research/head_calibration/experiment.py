"""Check a specific head-calibration hypothesis using fixed original-engine searches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "layout"))
sys.path.insert(0, str(HERE))

import calibration
import corpus
import head_bias
import native_check
import permute_nnue
import teacher


PLAN_SHA = "83f554d89173705d991bf90905308d8f1eb2999c00ff4948e9ba5948f7dd077b"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tag(rows, layout):
    tagged = []
    for row in rows:
        raw, physical = head_bias.head_for_fen(row["fen"], layout)
        tagged.append({**row, "raw_route": raw, "physical_head": physical})
    return tagged


def select_rows(tagged, target, control=None):
    selected = []
    for row in tagged:
        if row["physical_head"] == target:
            selected.append({**row, "role": "target"})
        elif control is not None and row["physical_head"] == control:
            selected.append({**row, "role": "control"})
    return selected


def labels(exe, net, rows, output, work, stem):
    result = teacher.collect_labels(exe, net, rows, output, work, stem)
    require(len(rows) == len(result), "Teacher dropped diagnostic positions")
    for row, item in zip(rows, result, strict=True):
        for field in ("position_id", "family", "physical_head", "role"):
            require(item[field] == row[field], "Teacher position identity changed")
        require(type(item["stable"]) is bool, "Missing stability decision")
        require(item["used_bucket"] == row["physical_head"], "Static trace selected another physical head")
    save(output / (stem + "-labels.json"), result)
    return result


def check_static_edit(original, candidate, target_head, sign):
    require(len(original) == len(candidate), "Candidate static cohort changed")
    for before, after in zip(original, candidate, strict=True):
        require(before["position_id"] == after["position_id"], "Candidate static position differs")
        require(before["used_bucket"] == after["used_bucket"] == before["physical_head"], "Physical route changed")
        prior, edited = before["buckets"], after["buckets"]
        require(len(prior) == len(edited) == 92, "Incomplete printed head table")
        for index, (a, b) in enumerate(zip(prior, edited, strict=True)):
            require(a["index"] == b["index"] == index and a["used"] == b["used"], "Head index or selection differs")
            if index != target_head:
                require(a == b, "An untouched head changed its printed output")
        difference = after["raw_score"] - before["raw_score"]
        if before["physical_head"] == target_head:
            require(sign * difference in (17, 18, 19), "Selected raw integer moved outside the expected bias effect")
        else:
            require(difference == 0, "Untouched selected raw integer changed")


def qualification(exe, candidate, output, work, exe_sha, net_sha):
    relative = ".\\" + candidate.name
    runs = []
    for index in (1, 2):
        stem = "qualification-bench-" + str(index)
        stdout, stderr = native_check._capture(exe, work, output, stem, native_check._commands(relative, "bench", []),
                                               relative, exe_sha, net_sha)
        block, = native_check._ready_blocks(stdout, stderr, 1)
        searches = native_check._ack_blocks(block, relative, 51)
        markers = re.findall(r"^Position: (\d+)/51 \(([^()]+)\)\s*$", stderr, re.M)
        require([int(i) for i, _ in markers] == list(range(1, 52)), "Incomplete benchmark position roster")
        positions = [native_check._bench_search(search, int(i), fen) for search, (i, fen) in zip(searches, markers, strict=True)]
        total = sum(pos["nodes"] for pos in positions)
        require(total > 0 and re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", stderr, re.M) == [str(total)], "Benchmark total differs")
        runs.append({"nodes": total, "positions": positions})
        save(output / (stem + ".parsed.json"), runs[-1])
    require(runs[0] == runs[1], "Candidate benchmark was not repeatable")
    stdout, stderr = native_check._capture(exe, work, output, "qualification-perft", native_check._commands(relative, "perft", []),
                                           relative, exe_sha, net_sha)
    perft = native_check._parse_perft(stdout, stderr, relative)
    save(output / "qualification-perft.parsed.json", perft)
    return {"bench_signatures": [v["nodes"] for v in runs], "perft4": perft["nodes"]}


def run(inputs, output):
    inputs, output = inputs.resolve(strict=True), output.resolve(strict=True)
    plan_path = HERE / "plan.json"
    require(digest(plan_path) == PLAN_SHA, "Prospective head diagnostic plan changed")
    plan = json.loads(plan_path.read_bytes())
    save(output / "PLAN.json", plan)
    net, exe = inputs / "parent.nnue", inputs / "reference.exe"
    require(digest(exe) == plan["accepted_executable_sha256"], "Reference executable differs")
    layout = permute_nnue.verify_parent(net.read_bytes())
    c = corpus.build_corpus(inputs / "edited.pgn", inputs / "parent.pgn")
    save(output / "CORPUS.json", c)
    result = {"experiment": plan["experiment"], "driver": os.environ.get("GITHUB_SHA"),
              "plan_sha256": PLAN_SHA, "corpus_sha256": digest(output / "CORPUS.json"),
              "heldout_gate_passed": False, "verified_elo_gain": False}
    if c["status"] != "ready":
        return {**result, "status": "corpus_infeasible", "issues": c["issues"]}
    training = tag(c["optimization"], layout)
    selection = calibration.select_heads(training)
    save(output / "HEAD-SELECTION.json", selection)
    target, control = selection["target_head"], selection["control_head"]
    result.update(target_head=target, control_head=control)
    with tempfile.TemporaryDirectory(prefix="bias-work-", dir=output.parent) as temp:
        work = Path(temp)
        training_rows = select_rows(training, target, control)
        train = labels(exe, net, training_rows, output, work, "train-original")
        fit = calibration.fit_bias(train, target, control)
        save(output / "FIT.json", fit)
        require(fit["status"] in ("frozen", "abstain"), "Unexpected fit decision")
        result.update(training_rows=len(train), stable_training_rows=sum(x["stable"] for x in train), fit_status=fit["status"])
        if fit["status"] == "abstain":
            return {**result, "status": "ended_at_training_diagnostic"}

        delta = fit["fc2_bias_delta"]
        require(delta in (-512, 512), "Unexpected fitted bias")
        candidate = work / "candidate.nnue"
        edit = head_bias.write_bias_candidate(net, target, delta, candidate)
        require(edit["parent_sha256"] == edit["inverse_sha256"] == plan["original_net_sha256"], "Parent inverse check failed")
        require(digest(candidate) == edit["candidate_sha256"], "Edited bytes differ from receipt")
        save(output / "FROZEN-EDIT.json", edit)
        frozen_edit_sha = digest(output / "FROZEN-EDIT.json")

        # Search labels and candidate static values for holdout are first evaluated here.
        hold_rows = select_rows(tag(c["holdout"], layout), target)
        if not hold_rows:
            return {**result, "status": "no_holdout_target_rows", "frozen_edit_sha256": frozen_edit_sha}
        hold = labels(exe, net, hold_rows, output, work, "holdout-original")
        static = teacher.static_evals(exe, candidate, hold_rows, output, work, "holdout-candidate")
        check_static_edit(hold, static, target, 1 if delta > 0 else -1)
        control_rows = [r for r in training_rows if r["physical_head"] == control][:16]
        by_id = {row["position_id"]: row for row in train}
        controls = teacher.static_evals(exe, candidate, control_rows, output, work, "unchanged-controls")
        check_static_edit([by_id[r["position_id"]] for r in control_rows], controls, target, 1 if delta > 0 else -1)
        predictions = {row["position_id"]: row["static_cp"] for row in static}
        scored = calibration.evaluate_bias(hold, predictions, target)
        save(output / "HOLDOUT.json", scored)
        require(scored["status"] in ("pass", "fail", "abstain"), "Unexpected holdout decision")
        require(frozen_edit_sha == digest(output / "FROZEN-EDIT.json"), "Fitted edit changed during scoring")
        result.update(holdout_rows=len(hold), stable_holdout_rows=sum(x["stable"] for x in hold),
                      frozen_edit_sha256=frozen_edit_sha, holdout_status=scored["status"])
        if scored["status"] != "pass":
            return {**result, "status": "ended_at_holdout_diagnostic"}
        checked = qualification(exe, candidate, output, work, plan["accepted_executable_sha256"], edit["candidate_sha256"])
        require(digest(candidate) == edit["candidate_sha256"] and digest(net) == plan["original_net_sha256"], "Network changed during checks")
        candidate.rename(output / "candidate.nnue")
    return {**result, "status": "candidate_ready_for_development_games", "candidate_sha256": edit["candidate_sha256"],
            "heldout_gate_passed": True, "qualification": checked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    try:
        result = run(args.inputs, args.output)
        save(args.output / "RESULT.json", result)
    except Exception as exc:
        save(args.output / "FAILED.json", {"error_type": type(exc).__name__, "message": str(exc)})
        raise
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
