"""Pure selection, MultiPV2 parsing, signs and exclusions for a baseline study.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from collections import Counter
import hashlib
import re

import chess
import native_check as native
import teacher


DOMAIN = "sibling-diagnostic-v1|"
DEPTHS = (9, 13)
INFO = re.compile(
    r"info depth (\d+) seldepth (\d+) multipv (\d+) score (cp|mate) ([+-]?\d+)"
    r"(?: (upperbound|lowerbound))? nodes (\d+) nps \d+ hashfull \d+ tbhits (\d+) time \d+ pv(?: (.*))?"
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def position_id(board):
    return hashlib.sha256(fen4(board).encode("ascii")).hexdigest()


def fen4(board):
    return " ".join(board.fen(en_passant="legal").split()[:4])


def selection_rank(pid):
    return hashlib.sha256((DOMAIN + pid).encode("ascii")).hexdigest()


def select_roots(optimization):
    """Select at most four legal >=2-move roots per original optimization family."""
    grouped, audit = {}, []
    seen = set()
    for row in optimization:
        pid, family = row["position_id"], row["family"]
        require(pid not in seen, "Repeated original optimization position")
        seen.add(pid)
        board = chess.Board(row["fen"])
        require(position_id(board) == pid, "Original optimization FEN/identity mismatch")
        count = board.legal_moves.count() if board.is_valid() else 0
        reason = ("invalid_or_variant" if not board.is_valid() or board.chess960 else
                  "terminal" if board.is_game_over(claim_draw=False) else
                  "fewer_than_two_legal_moves" if count < 2 else None)
        record = {**row, "selection_rank_sha256": selection_rank(pid), "legal_moves": count,
                  "eligibility_reason": reason}
        audit.append(record)
        grouped.setdefault(family, [])
        if reason is None:
            grouped[family].append(record)
    selected = []
    families = []
    for family, rows in sorted(grouped.items()):
        ranked = sorted(rows, key=lambda row: (row["selection_rank_sha256"], row["position_id"]))
        chosen = ranked[:4]
        selected.extend({**row, "role": "optimization"} for row in chosen)
        families.append({"family": family, "original_positions": sum(r["family"] == family for r in audit),
                         "eligible_positions": len(ranked), "selected_positions": len(chosen),
                         "selected_ids": [r["position_id"] for r in chosen]})
    return {"schema": "sibling-diagnostic-training-selection-v1", "domain": DOMAIN,
            "selected": selected, "families": families, "eligibility_audit": audit,
            "counts": {"original_positions": len(audit), "families": len(families),
                       "selected_positions": len(selected),
                       "ineligible": dict(sorted(Counter(r["eligibility_reason"] for r in audit if r["eligibility_reason"]).items()))}}


def legal_pv(board, moves):
    replay = board.copy(stack=False)
    for move in moves:
        require(re.fullmatch(native.MOVE, move), "Malformed UCI move in emitted PV")
        require(replay.is_legal(chess.Move.from_uci(move)), "Illegal move in emitted PV")
        replay.push_uci(move)


def parse_search(lines, board, net, requested_depth):
    """Use the final two scored rows, never an earlier exact/valid replacement."""
    require(requested_depth in DEPTHS, "Unexpected requested depth")
    body, = native._ack_blocks(lines, net, 1)
    bests = [native.BESTMOVE.fullmatch(line) for line in body if line.startswith("bestmove")]
    require(len(bests) == 1 and bests[0] and body[-1] == bests[0][0], "Missing or malformed final bestmove")
    best, ponder = bests[0][1], bests[0][2]
    require(best != "(none)", "Nonterminal selected root returned no bestmove")
    legal_pv(board, [best] + ([ponder] if ponder else []))
    scored = []
    all_pvs = []
    malformed = []
    for line_index, line in enumerate(body):
        if not line.startswith("info depth ") or " score " not in line:
            continue
        match = INFO.fullmatch(line)
        if not match:
            # Retain the raw line and legally replay its PV for exposure closure,
            # without inferring its missing score/rank/depth fields.
            pv = line.split(" pv ", 1)[1].split() if " pv " in line else []
            legal_pv(board, pv)
            all_pvs.append({"line_index": line_index, "depth": None, "rank": None, "pv": pv})
            malformed.append(line_index)
            scored.append(None)
            continue
        depth, seldepth, rank, kind, value, bound, nodes, hits, move_text = match.groups()
        pv = move_text.split() if move_text else []
        legal_pv(board, pv)
        record = {"depth": int(depth), "seldepth": int(seldepth), "rank": int(rank),
                  "score_kind": kind, "score_value": int(value), "bound": bound,
                  "reported_nodes": int(nodes), "tbhits": int(hits), "pv": pv,
                  "cp": int(value) if kind == "cp" and bound is None else None,
                  "line_index": line_index}
        require(record["tbhits"] == 0, "Unexpected tablebase hit")
        require(record["rank"] in (1, 2), "Unexpected MultiPV rank")
        scored.append(record)
        all_pvs.append({key: record[key] for key in ("line_index", "depth", "rank", "pv")})
    # Bestmove/ponder may expose a continuation even when no valid final score
    # pair exists. Include it and the root in the same complete exposure ledger.
    all_pvs.append({"line_index": len(body) - 1, "depth": None, "rank": None,
                    "pv": [best] + ([ponder] if ponder else []), "source": "bestmove_ponder"})
    final = scored[-2:]
    result = {"requested_depth": requested_depth, "bestmove": best, "ponder": ponder,
              "ranks": final if len(final) == 2 and all(x is not None for x in final) else [],
              "reason": None, "margin_cp": None, "all_reported_pvs": all_pvs,
              "malformed_score_line_indices": malformed}
    if len(final) != 2 or any(x is None for x in final):
        result["reason"] = "missing_or_malformed_final_rank_pair"
    elif [x["rank"] for x in final] != [1, 2]:
        result["reason"] = "final_rank_order_or_completeness"
    elif any(x["depth"] != requested_depth for x in final):
        result["reason"] = "requested_depth_not_completed_for_both_ranks"
    elif any(not x["pv"] or x["reported_nodes"] <= 0 for x in final):
        result["reason"] = "empty_final_pv_or_nonpositive_nodes"
    elif final[0]["pv"][0] == final[1]["pv"][0]:
        result["reason"] = "duplicate_candidate_first_move"
    elif final[0]["pv"][0] != best:
        result["reason"] = "bestmove_disagrees_with_final_rank1"
    elif any(x["bound"] is not None for x in final):
        result["reason"] = "bounded_final_score"
    elif any(x["score_kind"] != "cp" for x in final):
        result["reason"] = "mate_final_score"
    else:
        result["margin_cp"] = final[0]["cp"] - final[1]["cp"]
    return result


def teacher_pair(shallow, deep):
    reasons = [f"depth{search['requested_depth']}_{search['reason']}" for search in (shallow, deep) if search["reason"]]
    stable = False
    if not reasons:
        a = [rank["pv"][0] for rank in shallow["ranks"]]
        b = [rank["pv"][0] for rank in deep["ranks"]]
        stable = a == b
        if not stable:
            reasons.append("ordered_top_two_disagreement")
        if shallow["margin_cp"] < 20:
            reasons.append("depth9_margin_below_20_cp")
        if deep["margin_cp"] < 20:
            reasons.append("depth13_margin_below_20_cp")
        if abs(shallow["margin_cp"] - deep["margin_cp"]) > 20:
            reasons.append("margin_difference_above_20_cp")
        if any(abs(rank["cp"]) > 250 for rank in deep["ranks"]):
            reasons.append("depth13_score_outside_250_cp")
    return {"ordered_top_two_stable": stable, "teacher_clear": not reasons,
            "teacher_exclusions": reasons, "depth9_margin_cp": shallow["margin_cp"],
            "depth13_margin_cp": deep["margin_cp"]}


def candidate_children(root, deep):
    """Return actual depth-13 candidate boards even for non-clear teacher pairs."""
    children = []
    if len(deep["ranks"]) != 2:
        return children
    first_moves = []
    for rank in deep["ranks"]:
        if not rank["pv"]:
            return []
        first_moves.append(rank["pv"][0])
    if len(set(first_moves)) != 2:
        return []
    for rank, move_text in enumerate(first_moves, 1):
        board = chess.Board(root["fen"])
        move = chess.Move.from_uci(move_text)
        require(board.is_legal(move), "Candidate is not an actual legal child")
        capture = board.is_capture(move)
        zeroing = board.is_zeroing(move)
        board.push(move)
        reason = ("terminal_checkmate" if board.is_checkmate() else "terminal_stalemate" if board.is_stalemate() else
                  "terminal_board_rule" if board.is_game_over(claim_draw=False) else
                  "in_check_static_unavailable" if board.is_check() else None)
        children.append({"root_position_id": root["position_id"], "rank": rank,
                         "position_id": position_id(board), "family": root["family"], "role": "optimization_child",
                         "fen": board.fen(en_passant="legal"), "move_uci": move_text,
                         "capture": capture, "zeroing_move": zeroing, "halfmove_clock": board.halfmove_clock,
                         "static_exclusion": reason})
    return children


def parse_static(prefix, block, net, row):
    """Reuse the pinned native eval parser; no numeric value is invented."""
    stdout = "\n".join(prefix + block)
    chunks = native._ready_blocks(stdout, "", 1)
    native._ack_blocks(chunks[0], net, 1)
    missing = [name for name, pattern in (("raw_score", native.RAW_SCORE), ("white_score", native.WHITE_SCORE),
                                         ("final_score", native.FINAL_EVAL))
               if not any(pattern.fullmatch(line) for line in block)]
    if missing:
        bucket_rows = [native.BUCKET_ROW.fullmatch(line) for line in block if line.startswith("|")]
        bucket_rows = [match for match in bucket_rows if match is not None]
        active = [int(match[1]) for match in bucket_rows if match[4]]
        used = None
        if len(bucket_rows) == native.BUCKET_COUNT and [int(match[1]) for match in bucket_rows] == list(range(native.BUCKET_COUNT)):
            require(len(active) == 1 and active[0] == row["physical_head"], "Unavailable static trace selects a different physical head")
            used = active[0]
        return {**row, "status": "static_unavailable", "reason": "numeric_static_absent",
                "missing_fields": missing, "static_cp_stm": None, "static_cp_parent": None,
                "used_bucket": used}
    parsed = teacher._parse_static(prefix, block, net, row)
    # _parse_static already converts the printed WHITE score to CHILD STM.
    # An actual child always has the opposite mover, hence the second negation.
    return {**row, "status": "measured", "reason": None, "static_cp_stm": parsed["static_cp"],
            "static_cp_parent": -parsed["static_cp"], "used_bucket": parsed["used_bucket"],
            "raw_score": parsed["raw_score"], "final_score_white_pawns": parsed["final_score_white_pawns"],
            "nnue_score_white_pawns": parsed["nnue_score_white_pawns"], "buckets": parsed["buckets"]}


def inversion(pair, children):
    available = len(children) == 2 and [c["rank"] for c in children] == [1, 2] and all(c["status"] == "measured" for c in children)
    margin = children[0]["static_cp_parent"] - children[1]["static_cp_parent"] if available else None
    return {"both_child_statics_available": available, "static_parent_margin_cp": margin,
            "gross_inversion": bool(pair["teacher_clear"] and available and margin <= -12),
            "inversion_assessable": bool(pair["teacher_clear"] and available)}


def add_exposures(exposure, root, search):
    """Account for every reported PV at every depth/rank, not only final rows."""
    for pv in search["all_reported_pvs"]:
        board = chess.Board(root["fen"])
        for ply in range(len(pv["pv"]) + 1):
            pid, canonical = position_id(board), fen4(board)
            row = exposure.setdefault(pid, {"position_id": pid, "fen4": canonical, "requests": set()})
            require(row["fen4"] == canonical, "Exposure position digest collision")
            row["requests"].add((root["position_id"], search["requested_depth"], pv["line_index"],
                                 pv["depth"], pv["rank"], ply))
            if ply < len(pv["pv"]):
                board.push_uci(pv["pv"][ply])
