"""OFFICIAL32-head D1 sibling study. No file/model/native work on import.

The four vendored D1 modules are reused unchanged. This distinct sparse method
never invokes the legacy45-point operator or its c009 native entrypoint.
"""
from collections import Counter, defaultdict
from fractions import Fraction
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

for name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "d1lib"))
from stage2 import features_v19 as features, nnue_v19
from stage4.d1_fc2_study import forward, weights
import chess

PLAN_SHA = "ff34dd8db34c08f51f96201ae74131ab2e10c58a35d06542dc06e9da96f61e92"
SOURCE = "7cdd5e89a39fe3071b67d91c7bb07d712044bf76"
TREE = "7086bc94bdb39564b678ec4812ecde485de58fb5"
ENGINE = "b67c37d0d45e3540e311392aeeb699e3a745f16e172bc3fc6f85e0782093842b"
DELTAS = (-4, -2, -1, 0, 1, 2, 4)


def need(ok, reason):
    if not ok: raise ValueError(reason)


def canonical(doc): return json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
def digest(doc): return hashlib.sha256(canonical(doc)).hexdigest()
def hash_file(path):
    with Path(path).open("rb") as stream: return hashlib.file_digest(stream, "sha256").hexdigest()
def reference(path): return {"path": Path(path).name, "bytes": Path(path).stat().st_size, "sha256": hash_file(path)}


def put(path, doc):
    with Path(path).open("xb") as output:
        output.write(canonical(doc) + b"\n"); output.flush(); os.fsync(output.fileno())
    return reference(path)


def policy():
    raw = (HERE / "plan.json").read_bytes()
    need(hashlib.sha256(raw).hexdigest() == PLAN_SHA, "Predeclared OFFICIAL plan changed")
    result = json.loads(raw)
    for name, ref in result["reused_unmodified_D1_modules"].items():
        data = (HERE / "d1lib" / name).read_bytes()
        need(len(data) == ref["bytes"] and hashlib.sha256(data).hexdigest() == ref["sha256"], "Unchanged D1 source pin differs: " + name)
    return result


def load_train(path):
    plan = policy(); raw = Path(path).read_bytes()
    need(len(raw) == plan["data"]["train_bytes"] and hashlib.sha256(raw).hexdigest() == plan["data"]["train_sha256"], "Exact public TRAIN input required")
    result = json.loads(raw)
    need(result["partition"] == "train" and result["schema"] == "lichess-public-multipv-teachers-v1"
         and len(result["rows"]) == 328 and all(component_binding(r) for r in result["rows"]),
         "Training-only328-root/fixed-component contract")
    return result


def component_binding(row):
    component = row["component_id"]
    need(isinstance(component, str) and len(component) == 64, "Fixed component identity required")
    partition_hash = hashlib.sha256(("lichess-multipv-readout-v1/component|" + component).encode("ascii")).hexdigest()
    expected = "validation" if int(partition_hash, 16) % 5 == 0 else "train"
    return row["component_split_sha256"] == partition_hash and row["split"] == expected


def partition_contract_digest():
    data = policy()["data"]
    return digest({key: data[key] for key in ("train_sha256", "validation_sha256", "component_partition_rule")})


def child_plan(bundle):
    """Pure board/routing/accounting. No model or predictions are consulted."""
    need(bundle["partition"] in ("train", "validation"), "Unknown partition")
    children, parents = [], []
    for parent_index, root in enumerate(bundle["rows"]):
        need(root["split"] == bundle["partition"] and [c["rank"] for c in root["candidates"]] == [1, 2], "Fixed source ranks/split required")
        reasons, pending = [], []
        for candidate in root["candidates"]:
            board = chess.Board(root["fen"])
            token, = candidate["standard_uci"]
            move = board.parse_uci(token); need(move in board.legal_moves, "Illegal source first move"); board.push(move)
            need(board.fen(en_passant="legal") == candidate["child"]["fen"] and board.is_check() is candidate["child"]["in_check"]
                 and board.is_game_over(claim_draw=False) is candidate["child"]["terminal"], "Source child metadata differs")
            if candidate["child"]["in_check"]: reasons.append("in_check_child_rank_" + str(candidate["rank"]))
            if candidate["child"]["terminal"]: reasons.append("terminal_child_rank_" + str(candidate["rank"]))
            context = forward.eval_context(board)
            pending.append({"source_root_index": 2 * parent_index + candidate["rank"] - 1, "parent_position_id": root["position_id"],
                            "rank": candidate["rank"], "child_position_id": candidate["child"]["position_id"], "root_fen6": board.fen(en_passant="legal"),
                            "component_id": root["component_id"], "component_split_sha256": root["component_split_sha256"], "fold": root["split"],
                            "head": features.stack_bucket(board), "rule50": context.rule50, "wdl_material": context.wdl_material,
                            "static_material": context.static_material, "simple_eval_stm": context.simple_eval_stm})
        item = {"parent_position_id": root["position_id"], "component_id": root["component_id"], "component_split_sha256": root["component_split_sha256"],
                "teacher_margin_cp": root["teacher_margin_cp"], "root_pov": root["root_pov"], "fold": root["split"], "reasons": reasons,
                "source_root_indices": [] if reasons else [row["source_root_index"] for row in pending], "heads": [row["head"] for row in pending]}
        if not reasons: children.extend(pending)
        parents.append(item)
    return children, parents


def map_heads(parent_heads):
    need(len(parent_heads) == 32, "OFFICIAL D1 has32 stacks")
    return {"schema": "official-d1-fc2-byte-map/1", "parent_sha256": weights.PARENT_SHA256, "parent_bytes": weights.FILE_BYTES,
            "source_commit": SOURCE, "source_tree": TREE, "heads": [{"head": index, "bias_offset": 135156072 + 35208 * index,
            "bias_before": head.bias, "weights": list(head.weights), "weight_offsets": list(range(135156076 + 35208 * index, 135156204 + 35208 * index))}
            for index, head in enumerate(parent_heads)]}


def cache_train(network_path, train_path, out):
    plan = policy(); bundle = load_train(train_path); children, parents = child_plan(bundle)
    need(len(children) == 586 and sum(bool(p["reasons"]) for p in parents) == 35, "Premeasurement328/35/586 denominator changed")
    out = Path(out); out.mkdir(exist_ok=False)
    raw = Path(network_path).read_bytes()
    need(len(raw) == weights.FILE_BYTES and hashlib.sha256(raw).hexdigest() == weights.PARENT_SHA256, "Original136247652-byte D1 required")
    network = nnue_v19.Network(raw, expected_sha256=weights.PARENT_SHA256)
    builder = forward.CacheBuilder(network)
    metadata = map_heads(builder.parent_heads)
    map_ref = put(out / "MAPPING.json", metadata)
    with (out / "TRAIN-CACHE.jsonl").open("xb") as output:
        for child in children:
            cached = builder.row(child)
            output.write(canonical({**cached.record(), "parent_position_id": child["parent_position_id"], "rank": child["rank"],
                                    "child_position_id": child["child_position_id"], "component_split_sha256": child["component_split_sha256"]}) + b"\n")
    need(hash_file(network_path) == weights.PARENT_SHA256, "Original changed during TRAIN cache")
    manifest = {"schema": "official-d1-train-cache/1", "plan_sha256": PLAN_SHA, "source_commit": SOURCE, "source_tree": TREE,
                "network_sha256": weights.PARENT_SHA256, "network_bytes": weights.FILE_BYTES, "train": reference(train_path),
                "cache": reference(out / "TRAIN-CACHE.jsonl"), "mapping": map_ref, "mapping_digest": digest(metadata),
                "parents": parents, "children": 586, "root_denominator": 328, "native_parity_required_before_fit": True,
                "component_partition_contract_sha256": partition_contract_digest(),
                "validation_read": False, "fit_performed": False}
    put(out / "CACHE-MANIFEST.json", manifest)
    return manifest


def load_cache(directory):
    policy(); directory = Path(directory); manifest = json.loads((directory / "CACHE-MANIFEST.json").read_bytes())
    need(manifest["plan_sha256"] == PLAN_SHA and manifest["network_sha256"] == weights.PARENT_SHA256 and manifest["source_commit"] == SOURCE, "Wrong OFFICIAL cache family")
    for key in ("cache", "mapping"):
        ref = manifest[key]; need(reference(directory / ref["path"]) == ref, "Cache/mapping bytes changed")
    mapping = json.loads((directory / manifest["mapping"]["path"]).read_bytes())
    need(digest(mapping) == manifest["mapping_digest"] and len(mapping["heads"]) == 32, "Wrong32-head map")
    rows = [json.loads(line) for line in (directory / manifest["cache"]["path"]).read_bytes().splitlines()]
    need(len(rows) == 586 and len({r["source_root_index"] for r in rows}) == 586 and all(r["fold"] == "train" for r in rows), "Cache coverage/split differs")
    return manifest, mapping, rows


def cached_row(record):
    context = forward.EvalContext(record["side_to_move"], record["rule50"], record["simple_eval_stm"], record["static_material"], record["wdl_material"])
    return forward.CacheRow(record["source_root_index"], record["root_fen6"], record["component_id"], record["fold"], record["head"],
                            tuple(record["input128"]), record["skip_i32"], context, record["base_fc2_i32"], record["base_raw_nnue_i32"],
                            record["base_static_zero_i32"], record["base_static_cp_stm"])


def score(record, edit=None):
    row = cached_row(record)
    combined = forward.i32(row.base_fc2_i32 + row.skip_i32 + (0 if edit is None else edit["delta"] * row.input128[edit["lane"]]))
    raw = forward.i32(forward.trunc_div(forward.trunc_div(combined * 9600, 16384), 16))
    static = forward.static_wrapper(raw, row.context, optimism=0)
    return raw, forward.to_cp(static, row.context.wdl_material)


def parity_request(rows, network_path, network_sha, edits=()):
    changed = {e["head"]: e for e in edits}
    need(all(r["fold"] == "train" for r in rows), "TRAIN native parity request only")
    commands = ["uci", "setoption name NumaPolicy value none", "setoption name Threads value 1", "setoption name Hash value 16",
                "setoption name UCI_Chess960 value false", "setoption name EvalFile value " + str(network_path), "isready"]
    expected = []
    for record in rows:
        raw, cp = score(record, changed.get(record["head"]))
        commands.extend(("position fen " + record["root_fen6"], "eval", "isready"))
        expected.append({"source_root_index": record["source_root_index"], "head": record["head"], "raw_nnue_i32": raw,
                         "static_cp_white": cp if record["side_to_move"] == "w" else -cp})
    commands.append("quit"); text = "\n".join(commands) + "\n"
    return {"schema": "official-d1-dynamic-native-parity-request/1", "stdin": text, "stdin_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "expected": expected, "eval_file": str(network_path), "eval_file_sha256": network_sha, "engine_sha256": ENGINE,
            "candidate_tail_sha256": "not_legacy_grid", "max_stdout_bytes": 8 * 1024**2, "source_commit": SOURCE, "source_tree": TREE}


def accept_parity(cache_dir, request, stdout, stderr, returncode, native_receipt, out):
    manifest, _, rows = load_cache(cache_dir)
    need(native_receipt["engine_sha256"] == ENGINE and native_receipt["source_commit"] == SOURCE and native_receipt["source_tree"] == TREE
         and native_receipt["stdin_sha256"] == request["stdin_sha256"] and native_receipt["network_sha256"] == request["eval_file_sha256"]
         and native_receipt["inputs_rehashed_before_after"] is True, "Current-official native provenance mismatch")
    result = forward.verify_eval_transcript(request, stdout, stderr, returncode)
    need(result["row_count"] == len(rows) == 586, "Every original/edited TRAIN child requires native parity")
    receipt = {"schema": "official-d1-train-native-parity/1", "status": "OFFICIAL_NATIVE_PARITY_PASSED", "plan_sha256": PLAN_SHA,
               "cache_manifest": reference(Path(cache_dir) / "CACHE-MANIFEST.json"), "network_sha256": request["eval_file_sha256"],
               "source_commit": SOURCE, "source_tree": TREE, "engine_sha256": ENGINE, "rows": 586,
               "native_receipt": native_receipt, "numeric_result": result, "validation_read": False}
    put(out, receipt); return receipt


def huber20(deficit):
    deficit = Fraction(deficit); need(deficit >= 0, "Nonnegative deficit")
    return deficit * deficit / 2 if deficit <= 20 else 20 * (deficit - 10)


def component_mean(values):
    groups = defaultdict(list)
    for component, value in values: groups[component].append(Fraction(value))
    need(groups, "No component loss rows")
    return sum((sum(v, Fraction()) / len(v) for v in groups.values()), Fraction()) / len(groups)


def tie(cost, delta, lane): return Fraction(cost), 0 if delta == 0 else 1, abs(delta), -1 if lane is None else lane, DELTAS.index(delta)
def fraction(value): return {"numerator": value.numerator, "denominator": value.denominator}


def fit_records(parents, rows, mapping):
    by_index = {r["source_root_index"]: r for r in rows}; groups = defaultdict(list); dispositions = []
    for parent in parents:
        reasons = list(parent["reasons"])
        if not reasons and parent["heads"][0] != parent["heads"][1]: reasons.append("cross_head_training_pair")
        if not reasons:
            a, b = [by_index[i] for i in parent["source_root_indices"]]
            need(a["head"] == b["head"] == parent["heads"][0], "Official cache/routing changed")
            groups[a["head"]].append((parent, a, b))
        dispositions.append({"parent_position_id": parent["parent_position_id"], "reasons": reasons})
    support = [{"head": h, "roots": len(items), "components": len({p[0]["component_id"] for p in items})} for h, items in sorted(groups.items())]
    selected = sorted((v for v in support if v["roots"] >= 24 and v["components"] >= 8), key=lambda v: (-v["roots"], v["head"]))[:4]
    choices, edits, audits = [], [], []
    def loss(items, edit):
        values = []
        for parent, a, b in items:
            margin = -score(a, edit)[1] + score(b, edit)[1]
            values.append((parent["component_id"], huber20(max(0, min(60, parent["teacher_margin_cp"]) - margin))))
        return component_mean(values)
    for summary in selected:
        head, items = summary["head"], groups[summary["head"]]
        baseline = loss(items, None); best = {"head": head, "lane": None, "delta": 0, "loss": baseline, "cost": baseline}; audit = []
        for lane in range(128):
            for delta in DELTAS:
                if not delta: continue
                old = mapping["heads"][head]["weights"][lane]
                if not -128 <= old + delta <= 127:
                    audit.append({"lane": lane, "delta": delta, "unavailable": "signed_int8_bound"}); continue
                edit = {"head": head, "lane": lane, "delta": delta}
                try: candidate_loss = loss(items, edit)
                except ValueError as exc:
                    audit.append({"lane": lane, "delta": delta, "unavailable": str(exc)}); continue
                cost = candidate_loss + Fraction(delta * delta, 8)
                audit.append({"lane": lane, "delta": delta, "loss": fraction(candidate_loss), "cost": fraction(cost)})
                if tie(cost, delta, lane) < tie(best["cost"], best["delta"], best["lane"]): best = {**edit, "loss": candidate_loss, "cost": cost}
        choices.append({**summary, "lane": best["lane"], "delta": best["delta"], "baseline_loss": fraction(baseline), "loss": fraction(best["loss"]), "cost": fraction(best["cost"])})
        if best["delta"]:
            lane = best["lane"]; old = mapping["heads"][head]["weights"][lane]
            edits.append({"head": head, "lane": lane, "delta": best["delta"], "before": old, "after": old + best["delta"], "offset": 135156076 + 35208 * head + lane})
        audits.append({"head": head, "fixed_roots": len(items), "candidates": audit})
    return {"choices": choices, "edits": edits, "support": support, "dispositions": dispositions, "candidate_audit": audits}


def fit_train(cache_dir, parity_path, out):
    manifest, mapping, rows = load_cache(cache_dir); parity = json.loads(Path(parity_path).read_bytes())
    need(parity["status"] == "OFFICIAL_NATIVE_PARITY_PASSED" and parity["network_sha256"] == weights.PARENT_SHA256
         and parity["cache_manifest"] == reference(Path(cache_dir) / "CACHE-MANIFEST.json") and parity["rows"] == 586
         and parity["source_commit"] == SOURCE and parity["source_tree"] == TREE and parity["engine_sha256"] == ENGINE,
         "Original CURRENT-OFFICIAL native parity is required before any fit")
    out = Path(out); out.mkdir(exist_ok=False)
    result = fit_records(manifest["parents"], rows, mapping)
    frozen = {"schema": "official-d1-sibling-joint-freeze/1", "plan_sha256": PLAN_SHA, "source_commit": SOURCE, "source_tree": TREE,
              "parent_sha256": weights.PARENT_SHA256, "parent_bytes": weights.FILE_BYTES, "cache_manifest": reference(Path(cache_dir) / "CACHE-MANIFEST.json"),
              "mapping_digest": digest(mapping), "parent_parity": reference(parity_path), **result, "identity": not result["edits"],
              "component_partition_contract_sha256": manifest["component_partition_contract_sha256"],
              "validation_opened": False, "iterative_reselection": False, "method": "supervised_sparse_sibling_ranking", "verified_gain": False}
    frozen["freeze_sha256"] = digest(frozen); put(out / "FROZEN.json", frozen); return frozen


def verify_freeze(frozen, mapping):
    need(frozen["schema"] == "official-d1-sibling-joint-freeze/1" and frozen["freeze_sha256"] == digest({k: v for k, v in frozen.items() if k != "freeze_sha256"})
         and frozen["plan_sha256"] == PLAN_SHA and frozen["mapping_digest"] == digest(mapping) and frozen["parent_sha256"] == weights.PARENT_SHA256
         and frozen["source_commit"] == SOURCE and frozen["source_tree"] == TREE,
         "Official joint freeze changed")
    need(len(frozen["edits"]) <= 4 and len({e["head"] for e in frozen["edits"]}) == len(frozen["edits"]), "Sparse32-head edit count differs")
    for edit in frozen["edits"]:
        need(0 <= edit["head"] < 32 and 0 <= edit["lane"] < 128 and edit["delta"] in (-4, -2, -1, 1, 2, 4), "Unknown official edit")
        need(edit["offset"] == 135156076 + 35208 * edit["head"] + edit["lane"] and edit["before"] == mapping["heads"][edit["head"]]["weights"][edit["lane"]]
             and edit["after"] == edit["before"] + edit["delta"] and -128 <= edit["after"] <= 127, "Official byte offset/before value differs")


def apply_candidate(network_path, cache_dir, frozen_path, destination):
    _, mapping, _ = load_cache(cache_dir); frozen = json.loads(Path(frozen_path).read_bytes()); verify_freeze(frozen, mapping)
    need(frozen["cache_manifest"] == reference(Path(cache_dir) / "CACHE-MANIFEST.json"), "Frozen training cache changed before byte application")
    need(not frozen["identity"] and frozen["edits"], "Identity produces no new playing candidate")
    destination = Path(destination); need(not destination.exists() and destination.suffix == ".nnue", "Fresh explicit candidate.nnue required")
    need(hash_file(network_path) == weights.PARENT_SHA256 and Path(network_path).stat().st_size == weights.FILE_BYTES, "Exact original D1 source required")
    with Path(network_path).open("rb") as source:
        _, heads = weights._read_tail_stream(source)
    need(map_heads(heads) == mapping, "Original serialized32-head readout differs")
    regions = [(e["offset"], struct.pack("b", e["before"]), struct.pack("b", e["after"])) for e in sorted(frozen["edits"], key=lambda e: e["offset"])]
    with Path(network_path).open("rb") as source, destination.open("xb") as target:
        audit = weights._copy_patched(source, target, weights.FILE_BYTES, regions)
    with destination.open("rb") as target: weights._verify_output(target, audit, regions)
    class Sink:
        def write(self, raw): return len(raw)
    with destination.open("rb") as target:
        inverse = weights._copy_patched(target, Sink(), weights.FILE_BYTES, [(o, b, a) for o, a, b in regions])
    need(audit["parent_sha256"] == inverse["candidate_sha256"] == weights.PARENT_SHA256 and audit["changed_byte_count"] == len(regions), "Sparse inverse/untouched-byte proof failed")
    need(hash_file(network_path) == weights.PARENT_SHA256, "Original D1 modified")
    recipe = {"schema": "official-d1-sparse-byte-recipe/1", "freeze_sha256": frozen["freeze_sha256"], "parent_sha256": weights.PARENT_SHA256,
              "candidate_sha256": audit["candidate_sha256"], "bytes": weights.FILE_BYTES, "edits": frozen["edits"], "biases_unchanged": True,
              "inverse_sha256": inverse["candidate_sha256"], "all_untouched_spans_rehashed": True, "candidate": reference(destination),
              "source_commit": SOURCE, "source_tree": TREE, "validation_opened": False, "verified_gain": False}
    put(destination.with_suffix(".recipe.json"), recipe); return recipe


def main():
    cli = argparse.ArgumentParser(description=__doc__); sub = cli.add_subparsers(dest="action", required=True)
    cmd = sub.add_parser("cache"); cmd.add_argument("--network", required=True); cmd.add_argument("--train", default=str(HERE / "train.json")); cmd.add_argument("--out", required=True)
    cmd = sub.add_parser("fit"); cmd.add_argument("--cache", required=True); cmd.add_argument("--parity", required=True); cmd.add_argument("--out", required=True)
    cmd = sub.add_parser("apply"); cmd.add_argument("--network", required=True); cmd.add_argument("--cache", required=True); cmd.add_argument("--freeze", required=True); cmd.add_argument("--out", required=True)
    args = cli.parse_args()
    if args.action == "cache": result = cache_train(args.network, args.train, args.out)
    elif args.action == "fit": result = fit_train(args.cache, args.parity, args.out)
    else: result = apply_candidate(args.network, args.cache, args.freeze, args.out)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__": main()
