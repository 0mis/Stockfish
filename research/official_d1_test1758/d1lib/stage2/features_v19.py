"""Canonical SFNNv19 feature extraction and integer inference.

The feature definitions and arithmetic follow Stockfish commit
ff7cee4244ec96ea488c402669e3674c0574dce6 (GPL-3.0). Arrays supplied by
``nnue_v19.Network`` are in serialized, architecture-independent order.
No SIMD load-time permutations belong in this implementation.

``transform_many`` is the activation-corpus interface: uint8 [N, 1024],
side-to-move 512 channels followed by the opponent's 512 channels. Nonzero
masks are ``activations != 0``. Raw accumulator row order is WHITE, BLACK.
Source agreement alone is not native validation; ``validate_native`` compares
complete integer evaluations with the pinned executable's diagnostic trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import subprocess
from typing import Iterable, Protocol

import chess
import numpy as np

L1 = 1024
PSQ_BLOCK_DIMENSIONS = 22528
THREAT_DIMENSIONS = 59808
PAIR_DIMENSIONS = 4560
COLORS = (chess.WHITE, chess.BLACK)


class NetworkArrays(Protocol):
    ft_biases: np.ndarray
    psq_weights: np.ndarray
    threat_weights: np.ndarray
    pair_weights: np.ndarray
    stacks: list


@dataclass(frozen=True)
class PerspectiveFeatures:
    """Feature rows for one perspective; pairs use local, not threat-offset IDs."""

    perspective: chess.Color
    block: int
    psq: tuple[int, ...]
    threats: tuple[int, ...]
    pairs: tuple[int, ...]


def _board(value: chess.Board | str) -> chess.Board:
    board = chess.Board(value) if isinstance(value, str) else value
    if not isinstance(board, chess.Board) or not board.is_valid():
        raise ValueError("Expected a legal standard-chess board/FEN")
    if board.chess960:
        raise ValueError("This diagnostic interface accepts standard chess only")
    return board


def _color(color: chess.Color) -> int:
    """python-chess WHITE=True; Stockfish WHITE=0."""
    return 0 if color == chess.WHITE else 1


def _piece(piece: chess.Piece) -> int:
    return piece.piece_type + 8 * _color(piece.color)


def _orientation(king: int, perspective: chess.Color) -> int:
    return (7 if chess.square_file(king) < 4 else 0) ^ (56 * _color(perspective))


def psq_block(board: chess.Board, perspective: chess.Color) -> int:
    if board.pieces_mask(chess.QUEEN, not perspective):
        return 2
    return int(bool(board.rooks | board.queens))


def psq_index(square: int, piece: chess.Piece, king: int,
              perspective: chess.Color, block: int = 0) -> int:
    """K32H3 global row, including the selected 22,528-row block."""
    flip = 56 * _color(perspective)
    oriented_king = king ^ flip
    king_bucket = (7 - chess.square_rank(oriented_king)) * 4
    king_bucket += min(chess.square_file(oriented_king), 7 - chess.square_file(oriented_king))
    plane = (10 if piece.piece_type == chess.KING else
             2 * (piece.piece_type - 1) + int(piece.color != perspective))
    return (block * PSQ_BLOCK_DIMENSIONS + king_bucket * 704 + plane * 64
            + (square ^ _orientation(king, perspective)))


def _empty_attacks(piece_type: int, square: int, color: chess.Color = chess.WHITE) -> int:
    board = chess.BaseBoard(None)
    board.set_piece_at(square, chess.Piece(piece_type, color))
    return board.attacks_mask(square)


@lru_cache(maxsize=1)
def _threat_luts() -> tuple[np.ndarray, np.ndarray]:
    """Reproduce FullThreats::init_threat_luts, including same-type exclusion."""
    lut1 = np.zeros((16, 16, 2), dtype=np.int32)
    lut2 = np.zeros((7, 64, 64), dtype=np.int32)
    offsets = [0] * 7
    for pt in range(chess.PAWN, chess.KING + 1):
        offset = 0
        for source in range(64):
            white = _empty_attacks(pt, source)
            black = _empty_attacks(pt, source, chess.BLACK) if pt == chess.PAWN else white
            for target in range(64):
                attacks = black if pt == chess.PAWN and target < source else white
                lut2[pt, source, target] = offset + (attacks & ((1 << target) - 1)).bit_count()
            if pt != chess.PAWN or 8 <= source <= 55:
                offset += white.bit_count()
        offsets[pt] = offset

    target_map = ((-1, 0, -1, 1, -1, -1), (0, 1, 2, 3, 4, -1),
                  (0, 1, 2, 3, -1, -1), (0, 1, 2, 3, -1, -1),
                  (0, 1, 2, 3, 4, -1), (-1, -1, -1, -1, -1, -1))
    valid_counts = (0, 4, 10, 8, 8, 10, 0)
    pieces = tuple(range(1, 7)) + tuple(range(9, 15))
    cumulative = 0
    for attacker in pieces:
        pt = attacker & 7
        count = valid_counts[pt]
        for attacked in pieces:
            target_type = attacked & 7
            mapped = target_map[pt - 1][target_type - 1]
            same_enemy = (attacker ^ attacked) == 8
            semi_excluded = pt == target_type and (same_enemy or pt != chess.PAWN)
            feature = cumulative + ((attacked >> 3) * (count // 2) + mapped) * offsets[pt]
            lut1[attacker, attacked, 0] = THREAT_DIMENSIONS if mapped < 0 else feature
            lut1[attacker, attacked, 1] = (
                THREAT_DIMENSIONS if mapped < 0 or semi_excluded else feature)
        cumulative += count * offsets[pt]
    if cumulative != THREAT_DIMENSIONS:
        raise AssertionError(f"Unexpected threat feature count: {cumulative}")
    lut1.flags.writeable = lut2.flags.writeable = False
    return lut1, lut2


def threat_index(attacker: chess.Piece, source: int, target: int,
                 attacked: chess.Piece, king: int, perspective: chess.Color) -> int:
    """Return row or an excluded sentinel >=59,808, as the native code does."""
    lut1, lut2 = _threat_luts()
    orient = _orientation(king, perspective)
    source, target = source ^ orient, target ^ orient
    swap = 8 * _color(perspective)
    return int(lut1[_piece(attacker) ^ swap, _piece(attacked) ^ swap, int(source < target)]
               + lut2[attacker.piece_type, source, target])


def pair_index(source: int, color: chess.Color, target: int,
               paired_color: chess.Color, king: int, perspective: chess.Color) -> int:
    """PP_3Wide local row; caller applies the own/adjacent-file eligibility rule."""
    orient = _orientation(king, perspective)
    source, target = source ^ orient, target ^ orient
    if not (8 <= source <= 55 and 8 <= target <= 55):
        raise ValueError("Pawn pair squares must be on ranks 2 through 7")
    a = 48 * int(color != perspective) + source - 8
    b = 48 * int(paired_color != perspective) + target - 8
    if a == b:
        raise ValueError("A pawn cannot form a pair with itself")
    hi, lo = max(a, b), min(a, b)
    return hi * (hi - 1) // 2 + lo


def extract_features(value: chess.Board | str) -> tuple[PerspectiveFeatures, PerspectiveFeatures]:
    """Extract exact sparse feature rows, ordered WHITE then BLACK.

    Attack relations include friendly defenders and pinned pieces, use actual
    occupancy for sliders, exclude kings, and deduplicate same-type relations
    through the source LUT rule. Pair rows are unordered pawn pairs on the same
    or neighboring file, irrespective of rank distance or color.
    """
    board = _board(value)
    pieces = sorted(board.piece_map().items())
    pawns = [(sq, pc) for sq, pc in pieces if pc.piece_type == chess.PAWN]
    result = []
    for perspective in COLORS:
        king = board.king(perspective)
        block = psq_block(board, perspective)
        psq = tuple(psq_index(sq, pc, king, perspective, block) for sq, pc in pieces)
        threats = []
        for source, attacker in pieces:
            pt = attacker.piece_type
            if pt == chess.KING:
                continue
            if pt == chess.PAWN:
                targets = board.knights | board.rooks
            elif pt in (chess.KNIGHT, chess.QUEEN):
                targets = board.occupied & ~board.kings
            else:
                targets = board.pawns | board.knights | board.bishops | board.rooks
            for target in chess.scan_forward(board.attacks_mask(source) & targets):
                index = threat_index(attacker, source, target, board.piece_at(target), king, perspective)
                if index < THREAT_DIMENSIONS:
                    threats.append(index)
        pairs = []
        for i, (source, pawn) in enumerate(pawns):
            for target, other in pawns[i + 1:]:
                if abs(chess.square_file(source) - chess.square_file(target)) <= 1:
                    pairs.append(pair_index(source, pawn.color, target, other.color, king, perspective))
        result.append(PerspectiveFeatures(perspective, block, psq,
                                          tuple(sorted(threats)), tuple(sorted(pairs))))
    return tuple(result)


def infer_accumulators(value: chess.Board | str, network: NetworkArrays) -> np.ndarray:
    """Return canonical int16[2,1024] accumulators, WHITE then BLACK."""
    result = np.empty((2, L1), dtype=np.int16)
    for i, features in enumerate(extract_features(value)):
        total = np.asarray(network.ft_biases, dtype=np.int32).copy()
        for table, indices in ((network.psq_weights, features.psq),
                               (network.threat_weights, features.threats),
                               (network.pair_weights, features.pairs)):
            if indices:
                total += table[np.asarray(indices, dtype=np.intp)].sum(axis=0, dtype=np.int32)
        # SIMD accumulator arithmetic wraps modulo 2**16, never saturates.
        result[i] = ((total + 32768) % 65536 - 32768).astype(np.int16)
    return result


def transform_accumulators(accumulators: np.ndarray, turn: chess.Color) -> np.ndarray:
    """Clip each paired raw half to [0,255], multiply, divide by512, then order by STM."""
    if np.shape(accumulators) != (2, L1):
        raise ValueError("Expected WHITE,BLACK accumulators with shape (2,1024)")
    clipped = np.clip(np.asarray(accumulators, dtype=np.int32), 0, 255)
    paired = ((clipped[:, :512] * clipped[:, 512:]) // 512).astype(np.uint8)
    return paired[[0, 1] if turn == chess.WHITE else [1, 0]].reshape(L1)


def transform(value: chess.Board | str, network: NetworkArrays) -> np.ndarray:
    board = _board(value)
    return transform_accumulators(infer_accumulators(board, network), board.turn)


def transform_many(values: Iterable[chess.Board | str], network: NetworkArrays) -> np.ndarray:
    rows = [transform(value, network) for value in values]
    return np.stack(rows) if rows else np.empty((0, L1), dtype=np.uint8)


def stack_bucket(value: chess.Board | str) -> int:
    board = _board(value)
    pc_bucket = (board.occupied.bit_count() - 1) // 4
    queens = (2 * bool(board.pieces_mask(chess.QUEEN, board.turn))
              + bool(board.pieces_mask(chess.QUEEN, not board.turn)))
    return pc_bucket * 4 + queens


def _trunc_div(value: int, denominator: int) -> int:
    return value // denominator if value >= 0 else -((-value) // denominator)


def _activations(values: np.ndarray, shift: int) -> np.ndarray:
    values = np.asarray(values, dtype=np.int64)
    squared = np.minimum(127, values * values >> (2 * shift + 7))
    linear = np.clip(values >> shift, 0, 127)
    return np.concatenate((squared, linear))


def propagate(transformed: np.ndarray, stack) -> int:
    """Integer native NetworkArchitecture output, before OutputScale division."""
    if np.shape(transformed) != (L1,):
        raise ValueError("Expected one 1024-channel transformed row")
    fc0 = np.asarray(stack.fc0_bias, dtype=np.int64) + stack.fc0_weights.astype(np.int64) @ transformed.astype(np.int64)
    first = _activations(fc0, 7)
    fc1 = np.asarray(stack.fc1_bias, dtype=np.int64) + stack.fc1_weights.astype(np.int64) @ first
    last = np.concatenate((first, _activations(fc1, 6)))
    fc2 = int(np.asarray(stack.fc2_bias).reshape(-1)[0]) + int((stack.fc2_weights.astype(np.int64) @ last).reshape(-1)[0])
    forwarded = fc2 + int(fc0[30]) - int(fc0[31])
    return _trunc_div(forwarded * 9600, 16384)


def evaluate_nnue(value: chess.Board | str, network: NetworkArrays) -> int:
    """Exact side-to-move NNUE internal value, before engine scale_evaluation."""
    board = _board(value)
    return _trunc_div(propagate(transform(board, network), network.stacks[stack_bucket(board)]), 16)


def native_nnue_values(values: Iterable[chess.Board | str], engine_path: str | Path,
                       net_path: str | Path, timeout: float = 60) -> list[int]:
    """Read native diagnostic evals using one process/thread; no searches or matches."""
    boards = [_board(value) for value in values]
    if any(board.is_check() for board in boards):
        raise ValueError("Native eval trace refuses positions with the side to move in check")
    executable, net = Path(engine_path).resolve(), Path(net_path).resolve()
    commands = ["uci", "setoption name Threads value 1", "setoption name Hash value 16",
                f"setoption name EvalFile value {net}", "isready"]
    for board in boards:
        commands.extend(("position fen " + board.fen(), "eval"))
    commands.append("quit")
    completed = subprocess.run([str(executable)], input="\n".join(commands) + "\n",
                               text=True, capture_output=True, timeout=timeout,
                               cwd=executable.parent, check=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    matches = re.findall(r"NNUE evaluation\s+([+-]?\d+)\s+\(side to move, internal units\)", completed.stdout)
    if len(matches) != len(boards):
        raise RuntimeError(f"Expected {len(boards)} native NNUE values; got {len(matches)}. "
                           + completed.stdout[-1500:] + completed.stderr[-500:])
    return [int(value) for value in matches]


def validate_native(values: Iterable[chess.Board | str], network: NetworkArrays,
                    engine_path: str | Path, net_path: str | Path,
                    timeout: float = 60) -> dict:
    """Compare full integer outputs; this does not prove every activation byte equal."""
    boards = [_board(value) for value in values]
    if not boards:
        raise ValueError("Native validation requires at least one position")
    python_values = [evaluate_nnue(board, network) for board in boards]
    native_values = native_nnue_values(boards, engine_path, net_path, timeout)
    mismatches = [{"index": i, "fen": board.fen(), "python": a, "native": b}
                  for i, (board, a, b) in enumerate(zip(boards, python_values, native_values)) if a != b]
    return {"positions": len(boards), "matches": len(boards) - len(mismatches),
            "mismatches": mismatches, "python_values": python_values, "native_values": native_values,
            "comparison": "exact integer NNUE output; activation bytes not directly instrumented"}
