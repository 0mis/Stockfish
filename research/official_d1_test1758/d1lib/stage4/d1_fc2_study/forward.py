"""Exact D1 cached readout and static/CP conversion; passive native entrypoint.

No network is loaded, labels consumed or process launched on import. CacheBuilder
accepts an explicitly loaded/authenticated baseline Network later. The native
entrypoint requires a Root-owned authenticated runner callback; there is no
subprocess launcher or fitting routine here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Callable, Iterable, Iterator, Mapping

import chess
import numpy as np
from stage2 import features_v19 as features
from . import weights

I32_MIN, I32_MAX = -(1 << 31), (1 << 31) - 1
TB_WIN_IN_MAX_PLY = 31507
PIECE_VALUES = {chess.PAWN: 208, chess.KNIGHT: 781, chess.BISHOP: 825, chess.ROOK: 1276, chess.QUEEN: 2538}
WDL_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
WDL_A = (-142.72052667, 372.35176398, -340.71073572, 415.23490212)
MAX_STDOUT_BYTES = 8 * 1024 * 1024


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def i32(value: int) -> int:
    require(type(value) is int and I32_MIN <= value <= I32_MAX, "Native int32 intermediate overflow")
    return value


def trunc_div(value: int, denominator: int) -> int:
    require(type(value) is int and type(denominator) is int and denominator > 0, "Signed integer division contract")
    return value // denominator if value >= 0 else -((-value) // denominator)


def round_away(value: float) -> int:
    """C++ std::round, intentionally different from parameter ties-to-even."""
    require(math.isfinite(value), "Nonfinite CP conversion")
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


@dataclass(frozen=True)
class EvalContext:
    side_to_move: str
    rule50: int
    simple_eval_stm: int
    static_material: int
    wdl_material: int

    def __post_init__(self):
        require(self.side_to_move in ("w", "b") and type(self.rule50) is int and self.rule50 >= 0, "Invalid root side/rule50")
        for value in (self.simple_eval_stm, self.static_material, self.wdl_material):
            i32(value)
        require(self.static_material >= 0 and self.wdl_material >= 0, "Negative material")


def eval_context(board: chess.Board) -> EvalContext:
    require(board.is_valid() and not board.chess960, "Legal standard-chess root required")
    counts = {(piece, color): len(board.pieces(piece, color)) for piece in PIECE_VALUES for color in (chess.WHITE, chess.BLACK)}
    own, other = board.turn, not board.turn
    simple = sum(PIECE_VALUES[piece] * (counts[piece, own] - counts[piece, other]) for piece in PIECE_VALUES)
    material = 521 * (counts[chess.PAWN, own] + counts[chess.PAWN, other])
    material += sum(PIECE_VALUES[piece] * (counts[piece, own] + counts[piece, other]) for piece in PIECE_VALUES if piece != chess.PAWN)
    wdl = sum(WDL_VALUES[piece] * (counts[piece, own] + counts[piece, other]) for piece in WDL_VALUES)
    return EvalContext("w" if board.turn == chess.WHITE else "b", board.halfmove_clock, simple, material, wdl)


def static_wrapper(raw_nnue: int, context: EvalContext, optimism: int = 0) -> int:
    """ff7cee evaluate.cpp scale_evaluation, preserving every integer division."""
    raw_nnue, optimism = i32(raw_nnue), i32(optimism)
    se = context.simple_eval_stm
    se_norm = trunc_div(i32(se * 1024), abs(se) + 1024)
    nnue_norm = trunc_div(i32(raw_nnue * 1024), abs(raw_nnue) + 1024)
    correl = trunc_div(i32(se_norm * nnue_norm), 512)
    value = i32(raw_nnue + trunc_div(i32(i32(raw_nnue + optimism) * correl), 65536))
    # The material product is explicitly int64 in native source.
    product = context.static_material * value
    require(-(1 << 63) <= product < (1 << 63), "Native int64 material product overflow")
    value = i32(value + trunc_div(product, 90649))
    value = i32(value - trunc_div(i32(value * context.rule50), 189))
    return min(TB_WIN_IN_MAX_PLY - 1, max(-TB_WIN_IN_MAX_PLY + 1, value))


def wdl_a(material: int) -> float:
    require(type(material) is int and material >= 0, "Invalid WDL material count")
    value = min(78, max(17, material)) / 58.0
    a = ((WDL_A[0] * value + WDL_A[1]) * value + WDL_A[2]) * value + WDL_A[3]
    require(math.isfinite(a) and a > 0, "Invalid D1 CP scale")
    return a


def continuous_cp(internal_value: int, material: int) -> float:
    return 100.0 * i32(internal_value) / wdl_a(material)


def to_cp(internal_value: int, material: int) -> int:
    return round_away(continuous_cp(internal_value, material))


def readout(input128: tuple[int, ...], skip_i32: int, head: weights.Head) -> dict:
    require(type(input128) is tuple and len(input128) == 128
            and all(type(value) is int and 0 <= value <= 127 for value in input128), "Exactly128 canonical uint7 inputs required")
    skip = i32(skip_i32)
    affine = i32(head.bias + sum(value * coefficient for value, coefficient in zip(input128, head.weights)))
    combined = i32(affine + skip)
    scaled = i32(trunc_div(combined * 9600, 16384))
    raw = i32(trunc_div(scaled, 16))
    return {"fc2_i32": affine, "combined_i32": combined, "scaled_i32": scaled, "raw_nnue_i32": raw}


@dataclass(frozen=True)
class CacheRow:
    source_root_index: int
    root_fen6: str
    component_id: str
    fold: str
    head: int
    input128: tuple[int, ...]
    skip_i32: int
    context: EvalContext
    base_fc2_i32: int
    base_raw_nnue_i32: int
    base_static_zero_i32: int
    base_static_cp_stm: int

    def record(self) -> dict:
        return {"source_root_index": self.source_root_index, "root_fen6": self.root_fen6,
                "component_id": self.component_id, "fold": self.fold, "head": self.head,
                "input128": list(self.input128), "skip_i32": self.skip_i32,
                "side_to_move": self.context.side_to_move, "rule50": self.context.rule50,
                "simple_eval_stm": self.context.simple_eval_stm, "static_material": self.context.static_material,
                "wdl_material": self.context.wdl_material, "base_fc2_i32": self.base_fc2_i32,
                "base_raw_nnue_i32": self.base_raw_nnue_i32, "base_static_zero_i32": self.base_static_zero_i32,
                "base_static_cp_stm": self.base_static_cp_stm}


class CacheBuilder:
    """Explicit later allocation: a caller provides the loaded original D1 net."""
    def __init__(self, network):
        require(network.sha256 == weights.PARENT_SHA256 and network.stacks_offset == weights.STACKS_OFFSET
                and len(network.stacks) == weights.HEADS, "Authenticated original D1 network required")
        self.network = network
        self.parent_heads = tuple(weights.Head(int(stack.fc2_bias[0]), tuple(int(value) for value in stack.fc2_weights.reshape(-1)))
                                  for stack in network.stacks)
        require(weights.pack_heads(self.parent_heads) == b"".join(network.data[weights.STACKS_OFFSET + h * weights.STACK_BYTES + weights.FC2_IN_STACK:
                                                                                 weights.STACKS_OFFSET + h * weights.STACK_BYTES + weights.FC2_IN_STACK + weights.FC2_BYTES]
                                                                 for h in range(weights.HEADS)), "Decoder fc2 view does not match canonical serialized tail")

    def row(self, label: Mapping) -> CacheRow:
        require(type(label.get("source_root_index")) is int and label["source_root_index"] >= 0, "Stable source_root_index required")
        fen = label["root_fen6"]
        require(isinstance(fen, str) and len(fen.split()) == 6 and "\n" not in fen and "\r" not in fen, "Exact six-field root FEN required")
        board = chess.Board(fen)
        require(board.is_valid() and not board.is_check(), "Static cache excludes invalid or in-check roots")
        context, head_index = eval_context(board), features.stack_bucket(board)
        require(0 <= head_index < 32, "D1 head routing outside32 stacks")
        for name, expected in {"head": head_index, "rule50": context.rule50, "wdl_material": context.wdl_material,
                               "static_material": context.static_material, "simple_eval_stm": context.simple_eval_stm}.items():
            require(name in label and type(label[name]) is int and label[name] == expected, "Label/root context mismatch: " + name)
        require(isinstance(label.get("component_id"), str) and label["component_id"]
                and label.get("fold") in ("train", "validation"), "Bound component/fold required")
        transformed = features.transform(board, self.network).astype(np.int64)
        stack = self.network.stacks[head_index]
        fc0 = stack.fc0_bias.astype(np.int64) + stack.fc0_weights.astype(np.int64) @ transformed
        require(all(I32_MIN <= int(value) <= I32_MAX for value in fc0), "fc0 int32 overflow")
        first = features._activations(fc0, 7)
        fc1 = stack.fc1_bias.astype(np.int64) + stack.fc1_weights.astype(np.int64) @ first
        require(all(I32_MIN <= int(value) <= I32_MAX for value in fc1), "fc1 int32 overflow")
        joined = np.concatenate((first, features._activations(fc1, 6)))
        values = tuple(map(int, joined))
        skip = i32(int(fc0[30]) - int(fc0[31]))
        baseline = readout(values, skip, self.parent_heads[head_index])
        # Independent existing scalar entrypoint catches accidental cache wiring.
        require(baseline["raw_nnue_i32"] == features._trunc_div(features.propagate(transformed, stack), 16), "Cached baseline differs from existing scalar forward")
        static = static_wrapper(baseline["raw_nnue_i32"], context, optimism=0)
        return CacheRow(label["source_root_index"], fen, label["component_id"], label["fold"], head_index, values, skip, context,
                        baseline["fc2_i32"], baseline["raw_nnue_i32"], static, to_cp(static, context.wdl_material))

    def rows(self, labels: Iterable[Mapping], check: Callable[[], None] = lambda: None) -> Iterator[CacheRow]:
        previous = -1
        for label in labels:
            check()
            require(type(label.get("source_root_index")) is int and label["source_root_index"] > previous, "Label stream must have increasing unique source_root_index")
            previous = label["source_root_index"]
            require(type(label.get("eligible")) is bool, "Explicit eligibility required")
            if not label["eligible"]:
                continue
            require(label.get("rejection_reasons") == [], "Eligible label retains rejection reasons")
            yield self.row(label)


def score_cache(row: CacheRow, candidate: weights.Candidate) -> dict:
    require(0 <= row.head < 32 and len(candidate.heads) == 32, "Cached head/candidate mismatch")
    result = readout(row.input128, row.skip_i32, candidate.heads[row.head])
    static = static_wrapper(result["raw_nnue_i32"], row.context, optimism=0)
    cp = to_cp(static, row.context.wdl_material)
    return {**result, "static_zero_i32": static, "static_cp_stm": cp,
            "static_cp_white": cp if row.context.side_to_move == "w" else -cp,
            "continuous_static_cp_stm": continuous_cp(static, row.context.wdl_material)}


def prepare_conformance_request(rows: Iterable[CacheRow], candidate: weights.Candidate, *, engine_path: str | Path,
                                eval_file: str | Path, expected_eval_sha256: str, timeout_seconds: int = 300) -> dict:
    """Prepare a later Root-run c009 UCI/eval request; never launch a process."""
    records = tuple(rows)
    require(0 < len(records) <= 512 and type(timeout_seconds) is int and 0 < timeout_seconds <= 600, "Finite conformance bounds required")
    executable, network = Path(engine_path).resolve(strict=True), Path(eval_file).resolve(strict=True)
    require("\n" not in str(network) and "\r" not in str(network), "Unsafe EvalFile path")
    require(weights.file_sha256(executable) == weights.ENGINE_SHA256, "Qualified c009 engine identity differs")
    require(weights.file_sha256(network) == expected_eval_sha256 and network.stat().st_size == weights.FILE_BYTES, "Candidate EvalFile identity/size differs")
    commands = ["uci", "setoption name NumaPolicy value none", "setoption name Threads value 1", "setoption name Hash value 16",
                "setoption name UCI_Chess960 value false", "setoption name EvalFile value " + str(network), "isready"]
    expected = []
    for row in records:
        board = chess.Board(row.root_fen6)
        require(board.is_valid() and not board.is_check() and features.stack_bucket(board) == row.head and eval_context(board) == row.context,
                "Conformance root/cache context differs")
        score = score_cache(row, candidate)
        expected.append({"source_root_index": row.source_root_index, "head": row.head,
                         "raw_nnue_i32": score["raw_nnue_i32"], "static_cp_white": score["static_cp_white"]})
        commands.extend(("position fen " + row.root_fen6, "eval", "isready"))
    commands.append("quit")
    stdin = "\n".join(commands) + "\n"
    return {"schema": "d1-fc2-eval-conformance-request-v1", "argv": [str(executable)], "stdin": stdin,
            "stdin_sha256": hashlib.sha256(stdin.encode()).hexdigest(), "engine_sha256": weights.ENGINE_SHA256,
            "eval_file": str(network), "eval_file_sha256": expected_eval_sha256, "candidate_tail_sha256": candidate.tail_sha256,
            "expected": expected, "timeout_seconds": timeout_seconds, "max_stdout_bytes": MAX_STDOUT_BYTES,
            "max_stderr_bytes": 65536, "native_execution_authorized_by_this_function": False}


def verify_eval_transcript(request: Mapping, stdout: bytes, stderr: bytes, returncode: int) -> dict:
    """Numeric transcript check only; external native provenance is still required."""
    require(returncode == 0 and not stderr and len(stdout) <= request["max_stdout_bytes"], "Native eval failed/stderr/output limit")
    text = stdout.decode("utf-8").replace("\r\n", "\n")
    require("\r" not in text and not re.search(r"\b(?:ERROR|CRITICAL)\b", text), "Native eval error or framing")
    require(sum(line == "uciok" for line in text.splitlines()) == 1, "Missing/duplicate UCI handshake")
    segments, pending = [], []
    for line in text.splitlines():
        if line == "readyok":
            segments.append(pending); pending = []
        else:
            pending.append(line)
    require(not any(line.strip() for line in pending) and len(segments) == len(request["expected"]) + 1, "Conformance ready-boundary count differs")
    require(not any(line.startswith("NNUE evaluation ") or line.startswith("Final evaluation") for line in segments[0]), "Unexpected evaluation before first root")
    raw_pattern = re.compile(r"^NNUE evaluation\s+([+-]?\d+) \(side to move, internal units\)$")
    cp_pattern = re.compile(r"^Final evaluation\s+([+-]?\d+\.\d{2}) \(white side\) \[with scaled NNUE, \.\.\.\]$")
    network_prefix = "info string NNUE evaluation using "
    all_messages = [line for line in text.splitlines() if line.startswith(network_prefix)]
    require(all_messages and all(line.startswith(network_prefix + request["eval_file"] + " (") for line in all_messages), "EvalFile loaded-path confirmation differs")
    results = []
    for expected, lines in zip(request["expected"], segments[1:]):
        raw = [int(match[1]) for line in lines if (match := raw_pattern.fullmatch(line))]
        cp = [Decimal(match[1]) * 100 for line in lines if (match := cp_pattern.fullmatch(line))]
        require(len(raw) == len(cp) == 1 and cp[0] == cp[0].to_integral_value(), "Missing/ambiguous raw-int or FinalCP record")
        require(raw[0] == expected["raw_nnue_i32"] and int(cp[0]) == expected["static_cp_white"], "Candidate native/scalar raw-int or FinalCP mismatch")
        results.append({"source_root_index": expected["source_root_index"], "head": expected["head"], "raw_nnue_match": True, "final_cp_match": True})
    return {"schema": "d1-fc2-eval-conformance-result-v1", "status": "NUMERIC_TRANSCRIPT_MATCH",
            "rows": results, "row_count": len(results), "heads_covered": sorted({row["head"] for row in results}),
            "engine_sha256": request["engine_sha256"], "eval_file_sha256": request["eval_file_sha256"],
            "candidate_tail_sha256": request["candidate_tail_sha256"], "stdin_sha256": request["stdin_sha256"],
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(), "requires_external_native_receipt_authentication": True,
            "strength_claim": False}


def conformance_entrypoint(rows: Iterable[CacheRow], candidate: weights.Candidate, *, engine_path: str | Path,
                          eval_file: str | Path, expected_eval_sha256: str, run_native: Callable[[dict], Mapping]) -> dict:
    """Future explicit Root-owned runner injection. No internal launcher exists."""
    require(callable(run_native), "An explicitly allocated Root-owned runner is required")
    request = prepare_conformance_request(rows, candidate, engine_path=engine_path, eval_file=eval_file, expected_eval_sha256=expected_eval_sha256)
    native = run_native(request)
    require(native["argv"] == request["argv"] and native["stdin_sha256"] == request["stdin_sha256"], "Runner request binding differs")
    receipt = native["receipt"]
    require(receipt["returncode"] == 0 and receipt.get("failure") is None and receipt.get("descendants_dead_verified") is True
            and receipt.get("cleanup_verified") is True, "Root runner terminal/cleanup evidence incomplete")
    result = verify_eval_transcript(request, native["stdout"], native["stderr"], receipt["returncode"])
    require(weights.file_sha256(request["argv"][0]) == request["engine_sha256"]
            and weights.file_sha256(request["eval_file"]) == request["eval_file_sha256"], "Engine or EvalFile changed across conformance")
    result["runner_receipt_reference"] = native["receipt_reference"]
    result["engine_and_evalfile_rehashed_unchanged"] = True
    return result


def self_test() -> dict:
    cases = []
    assert [trunc_div(value, 2) for value in (-5, -3, -1, 1, 3, 5)] == [-2, -1, 0, 0, 1, 2]
    assert [round_away(value) for value in (-2.5, -0.5, 0.5, 2.5)] == [-3, -1, 1, 3]
    assert weights.round_even_ratio(250, 100) == 2 and round_away(2.5) == 3
    cases.append("signed_truncation_and_distinct_CP_vs_parameter_rounding")
    context = EvalContext("w", 0, 0, 24940, 78)
    assert static_wrapper(100, context) == 127 and static_wrapper(-100, context) == -127
    assert static_wrapper(100, EvalContext("w", 189, 0, 24940, 78)) == 0
    assert abs(wdl_a(58) - 304.15540371) < 1e-10 and to_cp(304, 58) == 100 and to_cp(-304, 58) == -100
    cases.append("D1_material_rule50_and_WDL_CP_constants")
    head = weights.Head(-700, tuple((index % 9) - 4 for index in range(128)))
    values = tuple(index % 128 for index in range(128))
    result = readout(values, -901, head)
    affine = -700 + sum((index % 128) * ((index % 9) - 4) for index in range(128))
    assert result["fc2_i32"] == affine and result["raw_nnue_i32"] == trunc_div(trunc_div((affine - 901) * 9600, 16384), 16)
    changed = list(head.weights); changed[127] += 1
    assert readout(values, -901, weights.Head(head.bias, tuple(changed)))["fc2_i32"] - affine == 127
    changed = list(head.weights); changed[63] += 1
    assert readout(values, -901, weights.Head(head.bias, tuple(changed)))["fc2_i32"] - affine == 63
    cases.append("all128_inputs_including63_and127_are_real_coefficients")
    try: readout((127,) * 128, I32_MAX, weights.Head(I32_MAX, (127,) * 128))
    except ValueError: pass
    else: raise AssertionError("Unsafe int32 readout accepted")
    cases.append("native_integer_overflow_refused")
    request = {"max_stdout_bytes": MAX_STDOUT_BYTES, "eval_file": "test-network.nnue", "expected": [
        {"source_root_index": 1, "head": 0, "raw_nnue_i32": 17, "static_cp_white": -123}],
        "engine_sha256": weights.ENGINE_SHA256, "eval_file_sha256": "mock-only", "candidate_tail_sha256": "mock-only", "stdin_sha256": "mock-only"}
    transcript = ("id name synthetic protocol fixture\nuciok\nreadyok\ninfo string NNUE evaluation using test-network.nnue (fixture)\n"
                  "NNUE evaluation +17 (side to move, internal units)\nFinal evaluation -1.23 (white side) [with scaled NNUE, ...]\nreadyok\n").encode()
    assert verify_eval_transcript(request, transcript, b"", 0)["row_count"] == 1
    for bad in (transcript.replace(b"+17", b"+18"), transcript.replace(b"-1.23", b"-1.24"), transcript.replace(b"test-network.nnue", b"other.nnue"), transcript[:-8]):
        try: verify_eval_transcript(request, bad, b"", 0)
        except ValueError: pass
        else: raise AssertionError("Bad native-style fixture accepted")
    cases.append("passive_raw_int_FinalCP_EvalFile_and_boundary_controls")
    return {"status": "PASS", "case_count": len(cases), "cases": cases, "actual_network_or_labels_read": False,
            "files_written": False, "engines_started": False, "fit_performed": False, "native_claim": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(self_test(), indent=2))
