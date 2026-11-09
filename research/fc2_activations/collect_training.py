"""Train-only frozen child capture; no fitting, validation read or row dropping."""
import hashlib
import json
from pathlib import Path
import chess
import protocol as p

TRAIN_SHA = "aca000f226d2fdeff0a45461d8fd5af28623b7b8de9ba3c698382b81173ca7d8"
TRAIN_BYTES = 781403


def planned(path):
    raw = path.read_bytes()
    p.need(len(raw) == TRAIN_BYTES and hashlib.sha256(raw).hexdigest() == TRAIN_SHA, "Frozen public training input differs")
    doc = json.loads(raw)
    p.need(doc["schema"] == "lichess-public-multipv-teachers-v1" and doc["partition"] == "train"
           and doc["projection"] == "ordered-first-move-pairs-v1" and len(doc["rows"]) == 328,
           "Wrong teacher partition or prospective projection")
    requests, account = [], []
    for root in doc["rows"]:
        reasons = []
        p.need(root["split"] == "train" and [c["rank"] for c in root["candidates"]] == [1, 2], "Wrong fixed rank/split")
        parent = chess.Board(root["fen"])
        candidates = []
        for candidate in root["candidates"]:
            p.need(len(candidate["standard_uci"]) == 1, "Teacher projection is not a single first move")
            board = parent.copy(stack=False)
            token = candidate["standard_uci"][0]
            move = board.parse_uci(token)
            p.need(move in board.legal_moves, "Illegal frozen first move")
            board.push(move)
            child = candidate["child"]
            p.need(board.fen(en_passant="legal") == child["fen"] and board.is_check() is child["in_check"]
                   and board.is_game_over(claim_draw=False) is child["terminal"], "Frozen child flags/replay differ")
            fen4 = " ".join(board.fen(en_passant="legal").split()[:4])
            shredder4 = " ".join(board.fen(shredder=True, en_passant="legal").split()[:4])
            canonical = hashlib.sha256(fen4.encode("ascii")).hexdigest()
            aliases = sorted({hashlib.sha256(" ".join(board.fen(shredder=shredder, en_passant=ep).split()[:4]).encode("ascii")).hexdigest()
                              for shredder in (False, True) for ep in ("legal", "fen")})
            p.need(child["position_id"] == canonical and child["alias_ids"] == aliases, "Child canonical/spelling identity differs")
            if child["in_check"]: reasons.append("in_check_child_rank_" + str(candidate["rank"]))
            if child["terminal"]: reasons.append("terminal_child_rank_" + str(candidate["rank"]))
            candidates.append((candidate, board, token))
        entry = {"parent_position_id": root["position_id"], "component_id": root["component_id"],
                 "component_split_sha256": root["component_split_sha256"], "source_line": root["source_line"],
                 "source_raw_line_sha256": root["source_raw_line_sha256"], "fixed_unavailability_reasons": reasons,
                 "status": "unavailable_for_ordinary_static_loss" if reasons else "capture_required", "capture_row_ids": []}
        if not reasons:
            for candidate, board, token in candidates:
                index = len(requests)
                requests.append({"row_id": index, "parent_position_id": root["position_id"], "rank": candidate["rank"],
                                 "component_id": root["component_id"], "component_split_sha256": root["component_split_sha256"],
                                 "fen": root["fen"], "move": token, "child_fen": board.fen(en_passant="legal"),
                                 "child_position_id": candidate["child"]["position_id"], "root_cp": candidate["root_cp"],
                                 "child_alias_ids": candidate["child"]["alias_ids"],
                                 "source_white_cp": candidate["source_white_cp"], "root_pov": root["root_pov"]})
                entry["capture_row_ids"].append(index)
        account.append(entry)
    p.need(len(account) == 328 and len(requests) == 586 and sum(bool(r["fixed_unavailability_reasons"]) for r in account) == 35,
           "Frozen training denominator or premeasurement eligibility changed")
    return requests, account


def collect(executable, network, mapping, output, capture, emit):
    source = Path(__file__).resolve().parent / "train.json"
    requests, account = planned(source)
    commands = "\n".join("capture " + str(row["row_id"]) + " fen " + row["fen"] + " moves " + row["move"] for row in requests) + "\nquit\n"
    raw, errors = capture([executable, "--fc2-activations-v1", "--network", network], executable.parent,
                          "train-fc2-capture", commands, timeout=300)
    p.need(not errors.strip(), "Unexpected training capture stderr")
    traces = [json.loads(line) for line in raw.splitlines()]
    p.need([row["row_id"] for row in traces] == list(range(586)), "No row-specific recovery/drop is allowed")
    for request, trace in zip(requests, traces, strict=True):
        p.validate(trace, mapping=mapping, readout=p.serialized_readout(network, trace["physical_head"]),
                   expected_board=chess.Board(request["child_fen"]))
        p.need(trace["static_loss_available"] is True, "Frozen eligible child lost ordinary static target availability")
    map_raw = (output / "FC2-BYTE-MAP.json").read_bytes()
    map_file_sha = hashlib.sha256(map_raw).hexdigest()
    map_sha = hashlib.sha256(json.dumps(json.loads(map_raw), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with executable.open("rb") as stream: executable_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    with (output / "TRAIN-CAPTURES.jsonl").open("x", encoding="utf-8", newline="\n") as destination:
        for request, trace in zip(requests, traces, strict=True):
            enriched = {**trace, "child_position_id": request["child_position_id"], "position_id": request["child_position_id"],
                        "parent_position_id": request["parent_position_id"], "rank": request["rank"], "component_id": request["component_id"],
                        "component_split_sha256": request["component_split_sha256"], "parent_sha256": p.NETWORK,
                        "network_sha256": p.NETWORK, "parent_bytes": p.NETWORK_BYTES, "byte_map_sha256": map_sha,
                        "byte_map_file_sha256": map_file_sha, "child_alias_ids": request["child_alias_ids"],
                        "diagnostic_executable_sha256": executable_sha, "training_input_sha256": TRAIN_SHA}
            destination.write(json.dumps(enriched, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
    result = {"schema": "test1757-fc2-train-capture-accounting/1", "partition": "train", "train_sha256": TRAIN_SHA,
              "root_denominator": 328, "eligible_root_pairs": 293, "captured_children": 586, "unavailable_root_pairs": 35,
              "parents": account, "requests": requests, "validation_partition_read": False,
              "model_fit_or_weight_edit": False, "label_unit_or_teacher_engine_identity_inferred": False,
              "all_capture_failures_are_whole_job_holds": True}
    emit(output / "TRAIN-CAPTURE-ACCOUNTING.json", result)
    return {"root_denominator": 328, "eligible_pairs": 293, "captured_children": 586, "unavailable_pairs": 35,
            "training_input_sha256": TRAIN_SHA, "validation_partition_read": False, "fit_performed": False}
