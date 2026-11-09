"""Fit one NNUE readout-bias vector on the fixed public training partition."""
import argparse
from collections import Counter
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
import math
import mmap
import os
from pathlib import Path
import re
import sys
import tempfile

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
sys.path.insert(0, str(RESEARCH))
from head_calibration.experiment import qualification
from readout_ridge import bias_patch_set, ridge_fit
import corpus
import fetch_inputs
import head_bias
import permute_nnue
import teacher


PLAN_SHA = "0e1e9ea256663bd6e204258f149d1916d9e119786c7cdb8eb524484bfc5f9a03"
BASE = "e0d20001c9d90c7e8c18bef054d53deb4c00ec2c"
BASE_SOURCES = {
    "head_calibration/experiment.py": "bd5f1cede5b8d2a2372e73ae166bf0b5c21957cd405606cd905da03bb0711b3d",
    "head_calibration/head_bias.py": "600b912c01a95b306dcd42b6508ffe5d51b9bfd5dee749a1017047685f739051",
    "head_calibration/teacher.py": "53b577b4f8ec6c15409cacff1ee7b57e4cfa107a5098c230be6402a4aa3af1f4",
    "layout/corpus.py": "3496b596389e625b57beafb8a46a46f1fee9b60abcd33f04b222fb19bd61f1d1",
    "layout/features_v19.py": "b494c4e11dafdfc9598c3c591c0dca720f41498ae5944d8dc384f03737a58e6e",
    "layout/fetch_inputs.py": "beab24bbfff53e4f919451a1114e3900fd68eb99ee71bd618f09cda080a0c75d",
    "layout/native_check.py": "580eef4ef8a33dc3ccc0634e44bef7716755eb77aa16a58ed0e922b12948b745",
    "layout/permute_nnue.py": "8cedd68e2d36437939e4987413a217ff7c3e21c560441feb94464828ada68d70",
}
HEADS = 92


def require(ok, message):
    if not ok:
        raise ValueError(message)


def ref(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": digest}


def save(path, doc):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(doc, stream, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")
    return ref(path)


def sources():
    result = {}
    for name, digest in BASE_SOURCES.items():
        entry = ref(RESEARCH / name)
        require(entry["sha256"] == digest, "Pinned research source changed: " + name)
        result[name] = entry
    for name in ("experiment.py", "bias_patch_set.py", "ridge_fit.py"):
        result["readout_ridge/" + name] = ref(HERE / name)
    require((teacher.SHALLOW_DEPTH, teacher.DEEP_DEPTH) == (9, 13), "Teacher depth contract changed")
    return result


def input_refs(inputs, plan):
    result = {name: ref(inputs / name) for name in ("parent.nnue", "reference.exe", "edited.pgn", "parent.pgn")}
    for name, pinned, digest in (("parent.nnue", fetch_inputs.NETWORK, plan["network_sha256"]),
                                 ("reference.exe", fetch_inputs.REFERENCE, plan["engine_sha256"])):
        row = result[name]
        pinned.verify(row["bytes"], row["sha256"])
        require(row["sha256"] == digest, "Input diverges from prospective plan: " + name)
    for arm, (_, size, digest) in corpus.SOURCES.items():
        row = result[arm + ".pgn"]
        require((row["bytes"], row["sha256"]) == (size, digest), "PGN source differs: " + arm)
    return result


def tag(rows, layout, role):
    tagged = []
    for row in rows:
        raw, head = head_bias.head_for_fen(row["fen"], layout)
        require(0 <= raw < 192 and 0 <= head < HEADS and layout.raw_to_physical[raw] == head, "Invalid physical route")
        tagged.append({**row, "raw_route": raw, "physical_head": head, "role": role})
    return tagged


def capture(exe, network, rows, evidence, work, stem, searches):
    collect = teacher.collect_labels if searches else teacher.static_evals
    measured = collect(exe, network, rows, evidence, work, stem)
    require(len(measured) == len(rows), "Native capture omitted positions: " + stem)
    for row, data in zip(rows, measured, strict=True):
        require(all(row[key] == data[key] for key in ("position_id", "family", "fen", "role", "physical_head"))
                and data["used_bucket"] == row["physical_head"], "Captured position/route changed: " + stem)
        require(type(data["static_cp"]) is int and type(data["raw_score"]) is int, "Noninteger measured score")
        if searches:
            require(type(data["stable"]) is bool and data["parent_static_cp"] == data["static_cp"]
                    and data["shallow_search"]["requested_depth"] == 9 and data["deep_search"]["requested_depth"] == 13,
                    "Teacher score type or search depth differs")
    record = json.loads((evidence / (stem + ".record.json")).read_bytes())
    require(record["status"] == "complete" and record["returncode"] == 0 and not record["forced_cleanup"]
            and not record["reader_errors"] and record["log_readers_closed"] is True
            and record["teacher"] is searches and record["requested_depths"] == ([9, 13] if searches else [])
            and record["requested_rows"] == record["completed_rows"] == len(rows)
            and record["inputs_before"] == record["inputs_after"]
            and record["files"]["rows"] == ref(evidence / (stem + ".rows.json")),
            "Capture receipt does not bind complete clean evidence: " + stem)
    return measured


def _material(fen):
    pieces = Counter(fen.split()[0].lower())
    count = sum(pieces[piece] * weight for piece, weight in zip("pnbrq", (1, 3, 3, 5, 9), strict=True))
    return min(78, max(17, count))


@lru_cache(maxsize=65536)
def _printed_raw_interval(material, printed):
    """Invert the accepted engine's rounded UCI::to_cp display conservatively.

    src/uci.cpp uses round(100*raw/a) and a material polynomial. nnue_misc.cpp
    emits the sign of the original integer, including signed rounded zero.
    Widen by 1e-8 raw units at ties to allow host floating-point evaluation.
    """
    require(isinstance(printed, str) and re.fullmatch(r"(?:[+-][0-9]+\.[0-9]{2}|0\.00)", printed), "Malformed bucket value")
    if printed == "0.00":
        return 0, 0
    n = int(abs(Decimal(printed)) * 100)
    m = material / 58.0
    a = ((-142.72052667 * m + 372.35176398) * m - 340.71073572) * m + 415.23490212
    low = max(1, math.ceil((n - 0.5) * a / 100 - 1e-8))
    high = math.floor((n + 0.5) * a / 100 + 1e-8)
    require(low <= high, "No signed internal integer could produce the displayed score")
    return (-high, -low) if printed.startswith("-") else (low, high)


def _first_forwarded(raw):
    # NNUE scales F by 75/2048 and truncates toward zero. The zero bin spans
    # both signs. This gives the first integer F for which raw(F) >= raw.
    return (raw * 2048 + 74) // 75 if raw > 0 else (raw - 1) * 2048 // 75 + 1


def _possible_shift(before, after, delta):
    low = max(_first_forwarded(before[0]), _first_forwarded(after[0]) - delta)
    high = min(_first_forwarded(before[1] + 1) - 1, _first_forwarded(after[1] + 1) - 1 - delta)
    return low <= high


def check_traces(rows, original, changed, deltas):
    require(len(deltas) == HEADS and len(rows) == len(original) == len(changed), "Trace populations differ")
    evidence = []
    unchanged = altered = 0
    for row, old, new in zip(rows, original, changed, strict=True):
        head = row["physical_head"]
        require(old["position_id"] == new["position_id"] == row["position_id"]
                and old["fen"] == new["fen"] == row["fen"] and old["used_bucket"] == new["used_bucket"] == head,
                "Trace identity or selected physical head differs")
        ob, nb = old["buckets"], new["buckets"]
        require(len(ob) == len(nb) == HEADS, "Missing physical bucket")
        material = _material(row["fen"])
        changed_prints = []
        before_raw, after_raw = old["raw_score"], new["raw_score"]
        bound = bias_patch_set.raw_delta_bounds(deltas[head])
        require(bound[0] <= after_raw - before_raw <= bound[1] and
                _possible_shift((before_raw, before_raw), (after_raw, after_raw), deltas[head]),
                "Selected integer change is impossible for the quantized bias")
        for i, (a, b) in enumerate(zip(ob, nb, strict=True)):
            require(a["index"] == b["index"] == i and a["used"] is (i == head) and b["used"] is (i == head), "Bucket routing changed")
            if deltas[i] == 0:
                require(a == b, "Untouched bucket changed")
                unchanged += 1
            else:
                altered += 1
            lo = _printed_raw_interval(material, a["printed_value"])
            hi = _printed_raw_interval(material, b["printed_value"])
            require(_possible_shift(lo, hi, deltas[i]), "Displayed bucket change is impossible for its FC2 bias")
            if i == head:
                require(lo[0] <= before_raw <= lo[1] and hi[0] <= after_raw <= hi[1], "Selected raw integer and printed bucket disagree")
            if a["printed_value"] != b["printed_value"]:
                changed_prints.append(i)
        evidence.append({"position_id":row["position_id"], "raw_route":row["raw_route"], "physical_head":head,
                         "fc2_bias_delta":deltas[head], "original_raw":before_raw, "measured_raw":after_raw,
                         "raw_delta_bounds":list(bound), "changed_printed_heads":changed_prints})
    return {"rows":evidence, "positions":len(rows), "buckets_checked":HEADS*len(rows),
            "unchanged_bias_bucket_occurrences":unchanged, "changed_bias_bucket_occurrences":altered,
            "printed_value_basis":"STM normalized bucket scores, signed by the original NNUE integer; only the selected head exposes its exact raw integer."}


def edit(net, path, deltas, mode, layout, parent_sha):
    report = bias_patch_set.write_bias_patch_set(net, deltas, path, mode)
    identity = not any(deltas)
    require(report["mode"] == mode and report["deltas"] == deltas and report["identity"] is identity
            and report["parent_sha256"] == report["inverse_sha256"] == parent_sha and report["output_written"] is (not identity)
            and list(layout.raw_to_physical) == report["raw_to_physical"], "Edited network geometry, deltas or inverse changed")
    if identity:
        require(not path.exists() and report["candidate_sha256"] == parent_sha, "Unexpected identity artifact")
    else:
        require(ref(path)["sha256"] == report["candidate_sha256"] != parent_sha, "Edited network digest differs")
    return report


def study(exe, net, layout, data, evidence, work, context):
    parent_sha = context["inputs"]["parent.nnue"]["sha256"]
    training = tag(data["optimization"], layout, "optimization")
    probe_deltas = [512] * HEADS
    probe = work / "probe.nnue"
    probe_edit = edit(net, probe, probe_deltas, "probe", layout, parent_sha)
    probe_ref = save(evidence / "PROBE-EDIT.json", probe_edit)
    train = capture(exe, net, training, evidence, work, "train-original", True)
    probe_static = capture(exe, probe, training, evidence, work, "train-probe", False)
    save(evidence / "PROBE-TRACE-CHECK.json", check_traces(training, train, probe_static, probe_deltas))
    require(ref(probe)["sha256"] == probe_edit["candidate_sha256"], "Auxiliary probe changed")
    probe.unlink()
    fit = ridge_fit.fit_offsets(train, probe_static)
    fit_ref = save(evidence / "FIT.json", fit)
    deltas = fit["deltas"]
    require(isinstance(deltas, list) and len(deltas) == HEADS
            and all(type(d) is int and -256 <= d <= 256 and d % 16 == 0 for d in deltas)
            and fit["identity"] is (not any(deltas)), "Invalid fitted bias vector")
    candidate = work / "candidate.nnue"
    fitted = edit(net, candidate, deltas, "candidate", layout, parent_sha)
    frozen = {"plan_sha256":PLAN_SHA, "corpus_sha256":context["corpus_sha256"], "probe_edit":probe_ref, "fit":fit_ref,
              "training_labels":ref(evidence / "train-original.rows.json"), "probe_statics":ref(evidence / "train-probe.rows.json"),
              "holdout_search_labels_opened":False, "deltas":deltas, "edit":fitted}
    freeze = save(evidence / "FROZEN-EDIT.json", frozen)
    common = {**context, "frozen_edit":freeze, "fit":fit_ref, "training_positions":len(train),
              "stable_training_positions":sum(row["stable"] for row in train), "changed_physical_heads":sum(d != 0 for d in deltas)}
    if fitted["identity"]:
        return {**common, "status":"identity", "candidate_qualified_for_games":False, "holdout_labels_opened":False}

    # The first candidate measurement and every held-out label follow durable
    # publication of the fitted bytes/deltas and the raw training evidence pins.
    trained_candidate = capture(exe, candidate, training, evidence, work, "train-frozen-candidate", False)
    save(evidence / "TRAIN-CANDIDATE-TRACE-CHECK.json", check_traces(training, train, trained_candidate, deltas))
    holdout = tag(data["holdout"], layout, "holdout")
    hold = capture(exe, net, holdout, evidence, work, "holdout-original", True)
    held_candidate = capture(exe, candidate, holdout, evidence, work, "holdout-frozen-candidate", False)
    save(evidence / "HOLDOUT-CANDIDATE-TRACE-CHECK.json", check_traces(holdout, hold, held_candidate, deltas))
    diagnostic = ridge_fit.evaluate_offsets(hold, held_candidate)
    holdout_ref = save(evidence / "HOLDOUT.json", diagnostic)
    require(diagnostic["counts"]["missing_stable_candidate_rows"] == 0
            and diagnostic["counts"]["paired_stable_rows"] == sum(r["stable"] for r in hold), "Held-out diagnostic omitted measured stable rows")
    require(ref(evidence / "FROZEN-EDIT.json") == freeze and ref(candidate)["sha256"] == fitted["candidate_sha256"],
            "Frozen candidate changed during evaluation")
    verified = qualification(exe, candidate, evidence, work, context["inputs"]["reference.exe"]["sha256"], fitted["candidate_sha256"])
    require(verified["perft4"] == 197281 and len(verified["bench_signatures"]) == 2 and verified["bench_signatures"][0] > 0
            and verified["bench_signatures"][0] == verified["bench_signatures"][1], "Incomplete repeatable native qualification")
    require(ref(candidate)["sha256"] == fitted["candidate_sha256"] and ref(net)["sha256"] == parent_sha
            and ref(evidence / "FROZEN-EDIT.json") == freeze, "A frozen input changed during qualification")
    return {**common, "status":"candidate_ready_for_development_games", "candidate_qualified_for_games":True,
            "candidate_sha256":fitted["candidate_sha256"], "holdout_labels_opened":True, "holdout":holdout_ref,
            "holdout_positions":len(hold), "stable_holdout_positions":sum(row["stable"] for row in hold), "qualification":verified}


def run(inputs, evidence):
    require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true", "Real ridge measurements require the hosted Windows recipe")
    inputs, evidence = inputs.resolve(strict=True), evidence.resolve(strict=True)
    plan_file = HERE / "plan.json"
    require(ref(plan_file)["sha256"] == PLAN_SHA, "Prospective ridge plan changed")
    plan = json.loads(plan_file.read_bytes())
    require(plan["base_driver"] == BASE, "Wrong qualification source base")
    save(evidence / "PLAN.json", plan)
    source_ref = save(evidence / "SOURCES.json", sources())
    before = input_refs(inputs, plan)
    input_binding = save(evidence / "INPUTS.json", before)
    net, exe = inputs / "parent.nnue", inputs / "reference.exe"
    with net.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapping:
        layout = permute_nnue.verify_parent(mapping)
    data = corpus.build_corpus(inputs / "edited.pgn", inputs / "parent.pgn")
    corpus_ref = save(evidence / "CORPUS.json", data)
    require(data["status"] == "ready" and (len(data["optimization"]), len(data["holdout"])) == (1536,480)
            and (data["audit"]["optimization_families"], data["audit"]["holdout_families"]) == (48,15),
            "Public family split/position counts differ")
    context = {"experiment":plan["experiment"], "driver":os.environ["GITHUB_SHA"], "plan_sha256":PLAN_SHA,
               "source_manifest":source_ref, "input_manifest":input_binding, "inputs":before, "corpus_sha256":corpus_ref["sha256"],
               "score_basis":"Original-engine depth-13 primary CP and final static CP are both expressed for the side to move. Raw selected NNUE is in internal units.",
               "exposure":"Retired public games supply the fixed families. Earlier work saw optimization labels for two heads. The +512 auxiliary is measured on optimization only. New labels, static traces, and qualification searches are development usage.",
               "scope":"Held-out equal-family MAE is descriptive. Game qualification is structural and functional. The fixed local development screen and formal strength confirmation are separate.",
               "verified_elo_gain":False}
    with tempfile.TemporaryDirectory(prefix="readout-ridge-", dir=evidence.parent) as tmp:
        work = Path(tmp)
        result = study(exe, net, layout, data, evidence, work, context)
        require(input_refs(inputs, plan) == before and ref(HERE / "plan.json")["sha256"] == PLAN_SHA
                and ref(evidence / "CORPUS.json") == corpus_ref, "Source inputs or corpus changed during measurement")
        result["inputs_after_rechecked"] = True
        if result["candidate_qualified_for_games"]:
            target = evidence / "candidate.nnue"
            require(not target.exists() and ref(work / "candidate.nnue")["sha256"] == result["candidate_sha256"], "Final candidate binding differs")
            (work / "candidate.nnue").rename(target)
    return result


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
        save(args.output / "FAILED.json", {"experiment":"test1757-regularized-readout-v1", "status":"failed",
             "error_type":type(exc).__name__, "message":str(exc), "candidate_qualified_for_games":False,"verified_elo_gain":False})
        raise
    print(json.dumps({"status":result["status"], "candidate_qualified_for_games":result["candidate_qualified_for_games"],
                      "candidate_sha256":result.get("candidate_sha256")},sort_keys=True),flush=True)


if __name__ == "__main__":
    main()
