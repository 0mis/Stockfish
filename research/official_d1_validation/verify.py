"""Prospective OFFICIAL D1 heldout verifier; import does no model/data/native I/O."""
from collections import Counter
from decimal import Decimal
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys

OFFICIAL = Path(__file__).resolve().parents[1] / "official_d1"
# Local preparation keeps the source study in the adjacent ci-draft; a hosted
# overlay inherits research/official_d1 from its reviewed training driver.
if not OFFICIAL.exists():
    OFFICIAL = Path(__file__).resolve().parents[3] / "ci-draft/research/official_d1"
sys.path.insert(0, str(OFFICIAL))
import study
import heldout_design
import chess

PLAN = "ff34dd8db34c08f51f96201ae74131ab2e10c58a35d06542dc06e9da96f61e92"
DESIGN = "09e9446fc53eb418ad997edd450f7a401f0cf37f79489b286c9c6820881c6af4"
STUDY = "da0e423868f92306934a5fc9e2269e3e5b45fd1a1b34eff50c7cb3a5f4d8ae9a"
RUN, ARTIFACT = 42608176797, 13668397917
VALIDATION_SHA = "f542d111aa1c850c00da08beb6d45a8fa5b0447a6aa9df93d06350ee4ba5cd24"


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def source_gate():
    need(sha((OFFICIAL / "heldout_design.py").read_bytes()) == DESIGN
         and sha((OFFICIAL / "study.py").read_bytes()) == STUDY, "Reviewed OFFICIAL source design changed")
    need(sha((OFFICIAL / "release_inputs.py").read_bytes()) == "60a2d614dca8926170f70f75450682a699b7d86380bf5ba29646d36f61212c25"
         and sha((OFFICIAL / "official-source-manifest.json").read_bytes()) == "82699b276d86f5f3f1434d17ecb56d29b65b9f3f490a352981352b1510545b41",
         "Reviewed official release/source transport pins changed")
    plan = study.policy()
    need(study.PLAN_SHA == PLAN and plan["network_parent"]["heads"] == 32
         and plan["quantizer"]["multiply_bits"] == 64 and study.weights.FILE_BYTES == 136247652,
         "Distinct OFFICIAL32/int64 source policy required")
    return plan


def root_gate(raw, expected_sha):
    need(sha(raw) == expected_sha and len(expected_sha) == 64, "Exact Root acceptance file SHA required")
    root = json.loads(raw)
    need(root["schema"] == "official-d1-heldout-root-acceptance/1" and root["accepted"] is True
         and root["scope"] == "one_fixed_official_d1_validation", "Legitimate scoped Root acceptance required")
    need(root["training_run_id"] == RUN and root["training_artifact"]["id"] == ARTIFACT
         and root["source_commit"] == study.SOURCE and root["source_tree"] == study.TREE
         and root["engine_sha256"] == study.ENGINE and root["parent_sha256"] == study.weights.PARENT_SHA256
         and root["plan_sha256"] == PLAN and root["no_performance_gate"] is True, "Root official source/model scope differs")
    need(root["validation_sha256"] == VALIDATION_SHA and root["validation_bytes"] == 162650
         and root["validation_roots"] == 68, "Only the source-plan fixed68 heldout input is allowed")
    required = ("frozen", "cache_manifest", "mapping", "train_cache", "original_train_parity")
    if not root.get("identity", False):
        required += ("recipe", "candidate", "edited_train_parity")
    for key in required:
        ref = root["files"][key]
        p = Path(ref["member"])
        need(not p.is_absolute() and ".." not in p.parts and "\\" not in ref["member"] and ":" not in ref["member"]
             and len(ref["sha256"]) == 64 and type(ref["bytes"]) is int and ref["bytes"] > 0, "Invalid accepted training artifact member")
    return root


def verify_streams(original, candidate, size, edits, expected_parent, expected_candidate):
    """Prove complete forward/inverse bytes with no data/label access."""
    by_offset = {e["offset"]: e for e in edits}
    need(len(by_offset) == len(edits), "Repeated byte offsets")
    parent_hash, candidate_hash, inverse_hash = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    at, changed = 0, 0
    while at < size:
        amount = min(1024 * 1024, size - at)
        a, b = original.read(amount), candidate.read(amount)
        need(len(a) == len(b) == amount, "Truncated model stream")
        expected, restored = bytearray(a), bytearray(b)
        for offset, edit in by_offset.items():
            if at <= offset < at + amount:
                index = offset - at
                need(struct.unpack_from("<b", a, index)[0] == edit["before"]
                     and struct.unpack_from("<b", b, index)[0] == edit["after"], "Frozen signed coefficient bytes differ")
                struct.pack_into("<b", expected, index, edit["after"])
                struct.pack_into("<b", restored, index, edit["before"])
                changed += int(a[index] != b[index])
        need(bytes(expected) == b and bytes(restored) == a, "Non-frozen bytes or biases changed")
        parent_hash.update(a); candidate_hash.update(b); inverse_hash.update(restored)
        at += amount
    need(original.read(1) == candidate.read(1) == b"", "Model EOF differs")
    need(parent_hash.hexdigest() == inverse_hash.hexdigest() == expected_parent
         and candidate_hash.hexdigest() == expected_candidate and changed == len(edits), "Complete forward/inverse model SHA differs")
    return {"bytes": size, "parent_sha256": parent_hash.hexdigest(), "candidate_sha256": candidate_hash.hexdigest(),
            "inverse_sha256": inverse_hash.hexdigest(), "changed_bytes": changed, "all_other_bytes_and_biases_unchanged": True}


def model_gate(*, original_path, candidate_path, frozen, recipe, mapping):
    study.verify_freeze(frozen, mapping)
    if frozen["identity"]:
        need(not frozen["edits"], "Inconsistent identity freeze")
        return {"status": "IDENTITY_STOPPED_BEFORE_HELDOUT"}
    need(frozen["edits"] and frozen["validation_opened"] is False and frozen["iterative_reselection"] is False,
         "One nonidentity frozen official candidate required")
    need(recipe["schema"] == "official-d1-sparse-byte-recipe/1" and recipe["freeze_sha256"] == frozen["freeze_sha256"]
         and recipe["edits"] == frozen["edits"] and recipe["biases_unchanged"] is True
         and recipe["parent_sha256"] == recipe["inverse_sha256"] == study.weights.PARENT_SHA256
         and recipe["bytes"] == study.weights.FILE_BYTES and recipe["all_untouched_spans_rehashed"] is True,
         "Wrong official frozen byte recipe")
    need(Path(original_path).stat().st_size == Path(candidate_path).stat().st_size == study.weights.FILE_BYTES, "Full official D1 file sizes required")
    with Path(original_path).open("rb") as original:
        _, heads = study.weights._read_tail_stream(original)
    need(study.map_heads(heads) == mapping, "Actual original32-head serialized mapping differs")
    with Path(original_path).open("rb") as original, Path(candidate_path).open("rb") as candidate:
        result = verify_streams(original, candidate, study.weights.FILE_BYTES, frozen["edits"], study.weights.PARENT_SHA256, recipe["candidate_sha256"])
    result["status"] = "NONIDENTITY_FORWARD_INVERSE_BYTES_VERIFIED"
    return result


def run_gated(*, root_raw, root_sha, prepare_training, open_validation, evaluate):
    """Small injectable order boundary, used by real hosted runner and tests."""
    root = root_gate(root_raw, root_sha)
    context = prepare_training(root)
    if context["identity"]:
        return {"status": "IDENTITY_STOPPED_BEFORE_HELDOUT", "validation_opened": False}
    need(context["model_bytes_verified"] is True and context["edited_train_parity_verified"] is True,
         "Complete model/inverse and edited-training parity gates required")
    raw = open_validation(root)
    need(len(raw) == 162650 and sha(raw) == VALIDATION_SHA, "Fixed heldout bytes/SHA differ")
    return evaluate(root, context, json.loads(raw))


def child_plan(bundle, train_parents):
    """Preserve source paired losses; additionally cover each valid counterpart."""
    need(bundle["partition"] == "validation" and len(bundle["rows"]) == 68, "All68 fixed heldout parents required")
    train_components = {r["component_id"] for r in train_parents}
    train_roots = {r["parent_position_id"] for r in train_parents}
    children, parents, dispositions, seen = [], [], [], set()
    for index, root in enumerate(bundle["rows"]):
        need(study.component_binding(root) and root["split"] == "validation"
             and root["component_id"] not in train_components and root["position_id"] not in train_roots
             and root["position_id"] not in seen, "Frozen heldout component assignment/disjointness differs")
        seen.add(root["position_id"])
        need([c["rank"] for c in root["candidates"]] == [1, 2], "Source pair ranks differ")
        reasons, indices, heads = [], [], []
        for candidate in root["candidates"]:
            board = chess.Board(root["fen"])
            token, = candidate["standard_uci"]
            move = board.parse_uci(token); need(board.is_legal(move), "Illegal published first move"); board.push(move)
            metadata = candidate["child"]
            need(board.fen(en_passant="legal") == metadata["fen"] and board.is_check() is metadata["in_check"]
                 and board.is_game_over(claim_draw=False) is metadata["terminal"], "Published child state differs")
            source_index = 2 * index + candidate["rank"] - 1
            why = ("in_check" if metadata["in_check"] else "terminal" if metadata["terminal"] else None)
            head = study.features.stack_bucket(board); heads.append(head)
            disposition = {"source_root_index": source_index, "parent_position_id": root["position_id"], "rank": candidate["rank"],
                           "child_position_id": metadata["position_id"], "head": head, "reason": why}
            if why:
                reasons.append(why + "_child_rank_" + str(candidate["rank"]))
                disposition["status"] = "ordinary_static_unavailable"
            else:
                context = study.forward.eval_context(board)
                children.append({"source_root_index": source_index, "parent_position_id": root["position_id"], "rank": candidate["rank"],
                    "child_position_id": metadata["position_id"], "root_fen6": board.fen(en_passant="legal"), "component_id": root["component_id"],
                    "component_split_sha256": root["component_split_sha256"], "fold": "validation", "head": head,
                    "rule50": context.rule50, "wdl_material": context.wdl_material, "static_material": context.static_material, "simple_eval_stm": context.simple_eval_stm})
                indices.append(source_index); disposition["status"] = "ordinary_static_measurable"
            dispositions.append(disposition)
        parents.append({"parent_position_id": root["position_id"], "component_id": root["component_id"], "component_split_sha256": root["component_split_sha256"],
                        "teacher_margin_cp": root["teacher_margin_cp"], "root_pov": root["root_pov"], "fold": "validation", "heads": heads,
                        "reasons": reasons, "source_root_indices": [] if reasons else indices})
    _, source_parents = study.child_plan(bundle)
    need(parents == source_parents, "Source-plan paired-loss population changed")
    return children, parents, dispositions


def parity_request(records, network_path, network_sha, edits=()):
    need(all(r["fold"] == "validation" for r in records), "Heldout records only")
    changed = {edit["head"]: edit for edit in edits}
    commands = ["uci", "setoption name NumaPolicy value none", "setoption name Threads value 1", "setoption name Hash value 16",
                "setoption name UCI_Chess960 value false", "setoption name EvalFile value " + str(network_path), "isready"]
    expected = []
    for record in records:
        raw, cp = study.score(record, changed.get(record["head"]))
        commands += ["position fen " + record["root_fen6"], "eval", "isready"]
        expected.append({"source_root_index": record["source_root_index"], "head": record["head"], "raw_nnue_i32": raw,
                         "static_cp_white": cp if record["side_to_move"] == "w" else -cp})
    commands.append("quit"); text = "\n".join(commands) + "\n"
    return {"schema": "official-d1-heldout-native-parity-request/1", "stdin": text, "stdin_sha256": sha(text.encode()),
            "expected": expected, "eval_file": str(network_path), "eval_file_sha256": network_sha, "engine_sha256": study.ENGINE,
            "candidate_tail_sha256": "joint_sparse_official_freeze_not_legacy_grid", "max_stdout_bytes": 8 * 1024**2,
            "source_commit": study.SOURCE, "source_tree": study.TREE}


def accept_parity(request, stdout, stderr, returncode, records):
    numeric = study.forward.verify_eval_transcript(request, stdout, stderr, returncode)
    lines = stdout.decode().replace("\r\n", "\n").splitlines()
    ready = [i for i, line in enumerate(lines) if line == "readyok"]
    blocks = [lines[a + 1:b] for a, b in zip(ready, ready[1:])]
    need(len(blocks) == len(records) == numeric["row_count"], "Incomplete heldout direct measurements")
    measured = []
    for record, block in zip(records, blocks):
        raw = [int(m[1]) for line in block if (m := re.fullmatch(r"NNUE evaluation\s+([+-]?\d+) \(side to move, internal units\)", line))]
        white = [int(Decimal(m[1]) * 100) for line in block if (m := re.fullmatch(r"Final evaluation\s+([+-]?\d+\.\d{2}) \(white side\) \[with scaled NNUE, \.\.\.\]", line))]
        need(len(raw) == len(white) == 1, "Missing direct ordinary raw/CP")
        measured.append({"source_root_index": record["source_root_index"], "head": record["head"], "raw_nnue_i32": raw[0],
                         "ordinary_static_cp_white": white[0], "ordinary_static_cp_stm": white[0] if record["side_to_move"] == "w" else -white[0]})
    return {"status": "OFFICIAL_HELDOUT_RAW_AND_CP_MATCH", "numeric": numeric, "measurements": measured}


def loss_report(parents, records, baseline, candidate, edits, dispositions):
    need(baseline["status"] == candidate["status"] == "OFFICIAL_HELDOUT_RAW_AND_CP_MATCH", "No heldout losses before both native parities")
    original = {r["source_root_index"]: r for r in baseline["measurements"]}
    edited = {r["source_root_index"]: r for r in candidate["measurements"]}
    need(set(original) == set(edited) == {r["source_root_index"] for r in records}, "Incomplete direct heldout measurement set")
    changed = {edit["head"]: edit for edit in edits}
    by_index = {r["source_root_index"]: r for r in records}
    rows, a_losses, b_losses = [], [], []
    for parent in parents:
        if parent["reasons"]:
            rows.append({**parent, "status": "unavailable"}); continue
        a, b = parent["source_root_indices"]
        predicted_a = -study.score(by_index[a])[1] + study.score(by_index[b])[1]
        predicted_b = -study.score(by_index[a], changed.get(by_index[a]["head"]))[1] + study.score(by_index[b], changed.get(by_index[b]["head"]))[1]
        actual_a = -original[a]["ordinary_static_cp_stm"] + original[b]["ordinary_static_cp_stm"]
        actual_b = -edited[a]["ordinary_static_cp_stm"] + edited[b]["ordinary_static_cp_stm"]
        need((actual_a, actual_b) == (predicted_a, predicted_b), "Measured/predicted root margin differs")
        loss_a = study.huber20(max(0, min(60, parent["teacher_margin_cp"]) - actual_a))
        loss_b = study.huber20(max(0, min(60, parent["teacher_margin_cp"]) - actual_b))
        a_losses.append((parent["component_id"], loss_a)); b_losses.append((parent["component_id"], loss_b))
        rows.append({**parent, "status": "matched", "original_margin_cp": actual_a, "edited_margin_cp": actual_b,
                     "predicted_original_margin_cp": predicted_a, "predicted_edited_margin_cp": predicted_b,
                     "cross_head": parent["heads"][0] != parent["heads"][1], "touches_changed_head": bool(set(parent["heads"]).intersection(changed)),
                     "original_loss": study.fraction(loss_a), "edited_loss": study.fraction(loss_b)})
    a_mean = study.fraction(study.component_mean(a_losses)) if a_losses else None
    b_mean = study.fraction(study.component_mean(b_losses)) if b_losses else None
    return {"schema": "official-d1-heldout-verified-proxy/1", "root_denominator": len(parents), "matched_roots": len(a_losses),
            "matched_components": len({g for g, _ in a_losses}), "unavailable_roots": len(parents) - len(a_losses), "rows": rows,
            "child_dispositions": dispositions, "measured_children": len(records),
            "excluded_child_counts": dict(sorted(Counter(c["reason"] for c in dispositions if c["reason"]).items())),
            "overlapping_parent_exclusion_counts": dict(sorted(Counter(reason for p in parents for reason in p["reasons"]).items())),
            "measured_original_component_loss": a_mean, "measured_edited_component_loss": b_mean,
            "predicted_original_component_loss": a_mean, "predicted_edited_component_loss": b_mean,
            "matched_touching_changed_heads": sum(r.get("touches_changed_head", False) for r in rows),
            "matched_unchanged_heads": sum(r["status"] == "matched" and not r["touches_changed_head"] for r in rows),
            "matched_cross_head_pairs": sum(r.get("cross_head", False) for r in rows),
            "native_parity_required_and_passed": True, "no_performance_gate": True, "winner_decided": False,
            "no_validation_reselection_or_fallback": True, "verified_gain": False}


def parse_perft(stdout, stderr, returncode):
    need(returncode == 0 and not stderr, "Perft process failed")
    text = stdout.decode().replace("\r\n", "\n")
    pairs = [(m[1], int(m[2])) for line in text.splitlines() if (m := re.fullmatch(r"([a-h][1-8][a-h][1-8][qrbn]?): (\d+)", line))]
    divides = dict(pairs)
    need(text.splitlines().count("uciok") == 1 and text.splitlines().count("readyok") == 2
         and set(divides) == {m.uci() for m in chess.Board().legal_moves} and len(pairs) == len(divides) == 20
         and sum(divides.values()) == 197281 and re.findall(r"Nodes searched:\s*(\d+)", text) == ["197281"], "Complete standard perft4 differs")
    return {"nodes": 197281, "divides": divides}


def parse_bench(stdout, stderr, returncode):
    need(returncode == 0 and not re.search(rb"\b(?:ERROR|CRITICAL)\b", stdout + stderr, re.I), "Bench process failed")
    markers = re.findall(r"^Position: (\d+)/51 \(([^\r\n]+)\)\s*$", stderr.decode().replace("\r\n", "\n"), re.M)
    need(stdout.decode().splitlines().count("uciok") == 1 and stdout.decode().splitlines().count("readyok") == 2, "Bench UCI/ready handshake differs")
    need([int(i) for i, _ in markers] == list(range(1, 52)), "Complete51-position bench roster required")
    chunks, current = [], []
    for line in stdout.decode().replace("\r\n", "\n").splitlines():
        current.append(line)
        if line.startswith("bestmove "):
            chunks.append(current); current = []
    need(len(chunks) == 51 and not any(line.startswith(("info depth", "bestmove")) for line in current), "Complete51 bench searches required")
    vector = []
    for (index, fen), lines in zip(markers, chunks):
        board = chess.Board(fen)
        best = re.fullmatch(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?|\(none\))(?: ponder ([a-h][1-8][a-h][1-8][qrbn]?))?", lines[-1])
        need(best, "Malformed bench bestmove")
        scored = [line for line in lines if line.startswith("info depth ") and " score " in line]
        need(scored, "Missing bench final score")
        for line in scored:
            if " pv " in line:
                replay = board.copy(stack=False)
                for token in line.split(" pv ", 1)[1].split():
                    move = chess.Move.from_uci(token); need(replay.is_legal(move), "Illegal bench PV"); replay.push(move)
        if best[1] == "(none)":
            need(board.is_game_over(claim_draw=False) and best[2] is None, "Nonterminal bench none move")
        else:
            replay = board.copy(stack=False)
            for token in (best[1], best[2]):
                if token:
                    move = chess.Move.from_uci(token); need(replay.is_legal(move), "Illegal bench bestmove/ponder"); replay.push(move)
        final = scored[-1]
        if best[1] != "(none)":
            need(" pv " in final and final.split(" pv ", 1)[1].split()[0] == best[1], "Bench final PV/bestmove differs")
        nodes = re.search(r"\bnodes (\d+)\b", final)
        count = int(nodes[1]) if nodes else 0
        need(best[1] == "(none)" or count > 0, "Missing bench node count")
        vector.append({"index": int(index), "fen": fen, "final_info": re.sub(r"\s+(?:nps|time)\s+\d+", "", final),
                       "bestmove": lines[-1], "nodes": count})
    total = sum(r["nodes"] for r in vector)
    need(total > 0 and re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", stderr.decode(), re.M) == [str(total)], "Bench aggregate nodes differ")
    return {"nodes": total, "searches": 51, "vector": vector, "search_signature_sha256": sha(canonical(vector))}
