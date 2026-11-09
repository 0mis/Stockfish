"""Independent integer/layout validation; import starts no native process."""
import hashlib
import math
import struct

SOURCE = "7a17b53bcaf93f54a23ad65f309c413585500c82"
NETWORK = "18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf"
NETWORK_BYTES = 138360528
FALLBACK_SHA = "166ea64eb3e4da682ba3394b0a7f68de6ca6e01daae472f8c85fbc5ad7e27dd7"
I32_MIN, I32_MAX = -(1 << 31), (1 << 31) - 1


def need(ok, message):
    if not ok: raise ValueError(message)


def integer(value, low, high, name):
    need(type(value) is int and low <= value <= high, "Invalid integer " + name)
    return value


def tz(value, divisor): return value // divisor if value >= 0 else -((-value) // divisor)


def physical_slot(index):
    block, within = divmod(index, 32)
    chunk, lane = divmod(within, 4)
    return block * 32 + ((chunk % 2) * 4 + chunk // 2) * 4 + lane


def activations(values, shift):
    # Source-equivalent scalar result of AVX2 packs_epi32/mulhi/shift/packs_epi16.
    square = [min(127, (value * value) >> (2 * shift + 7)) for value in values]
    linear = [min(127, max(0, value >> shift)) for value in values]
    return square + linear


def raw_route(board):
    import chess
    band = (board.occupied.bit_count() - 1) // 4
    queens = 2 * bool(board.pieces(chess.QUEEN, board.turn)) + bool(board.pieces(chess.QUEEN, not board.turn))
    pawns = board.pawns
    left = sum(chess.BB_FILES[:4])
    layout = 2 if not pawns else 0 if pawns & left and pawns & ~left else 1
    officers = board.occupied & ~(board.pawns | board.kings)
    many = max((officers & board.occupied_co[color]).bit_count() for color in chess.COLORS) >= 2
    return ((band * 4 + queens) * 3 + layout) * 2 + int(many)


def static_zero(raw, board):
    import chess
    values = (208, 781, 825, 1276, 2538)
    counts = [[len(board.pieces(piece, color)) for piece in range(1, 6)] for color in (board.turn, not board.turn)]
    se = sum(value * (own - other) for value, own, other in zip(values, *counts))
    se_norm = tz(se * 1024, abs(se) + 1024)
    nnue_norm = tz(raw * 1024, abs(raw) + 1024)
    correlation = tz(se_norm * nnue_norm, 512)
    result = raw + tz(raw * correlation, 65536)
    material = sum((521 if index == 0 else values[index]) * (counts[0][index] + counts[1][index]) for index in range(5))
    result += tz(material * result, 90649)
    result -= tz(result * board.halfmove_clock, 189)
    return min(31506, max(-31506, result))


def to_cp(value, board):
    import chess
    material = sum(multiplier * len(board.pieces(piece, color)) for color in chess.COLORS
                   for piece, multiplier in ((chess.PAWN, 1), (chess.KNIGHT, 3), (chess.BISHOP, 3), (chess.ROOK, 5), (chess.QUEEN, 9)))
    m = min(78, max(17, material)) / 58.0
    a = (((-142.72052667 * m + 372.35176398) * m - 340.71073572) * m) + 415.23490212
    unrounded = 100 * value / a
    return math.floor(unrounded + 0.5) if unrounded >= 0 else -math.floor(-unrounded + 0.5)


def network_layout(path):
    need(path.stat().st_size == NETWORK_BYTES, "Original network size changed")
    with path.open("rb") as stream: prefix = stream.read(512)
    need(struct.unpack_from("<3I", prefix) == (0x6A448AFA, 0x9A4FCB66, 84), "NNUE header differs")
    need(struct.unpack_from("<3I", prefix, 96) == (0x5A7AB1E5, 192, 92), "Fallback geometry differs")
    table = prefix[108:492]
    need(hashlib.sha256(table).hexdigest() == FALLBACK_SHA, "Original fallback bytes changed")
    fallback = struct.unpack("<192H", table)
    roots = [index for index, parent in enumerate(fallback) if index == parent]
    need(len(roots) == 92, "Stored head count differs")
    physical = {root: index for index, root in enumerate(roots)}
    need(all(parent in physical for parent in fallback), "Non-self fallback root")
    need(struct.unpack_from("<I", prefix, 492)[0] == 0xA3060B95, "Transformer marker differs")
    return [physical[parent] for parent in fallback]


def serialized_readout(path, head):
    integer(head, 0, 91, "physical_head")
    start = NETWORK_BYTES - 92 * 35208 + head * 35208
    with path.open("rb") as stream:
        stream.seek(start)
        need(stream.read(4) == struct.pack("<I", 0x63337116), "Stored stack tag differs")
        stream.seek(start + 35076)
        raw = stream.read(132)
    need(len(raw) == 132, "Short FC2 readout")
    bias, *weights = struct.unpack("<i128b", raw)
    return bias, weights


def validate(row, *, mapping=None, readout=None, expected_board=None):
    need(row["schema"] == "test1757-fc2-activation/1" and row["source_commit"] == SOURCE, "Unknown activation/source schema")
    integer(row["row_id"], 0, 2**32 - 1, "row_id")
    need(row["integer_parity"] is True and row["history_scope"] == "supplied_fen_and_legal_prefix_only", "Missing parity/history scope")
    need(row["stored_heads"] == 92, "Unexpected stored head count")
    head = integer(row["physical_head"], 0, 91, "physical_head")
    for name, size, low, high in (("fc0", 32, I32_MIN, I32_MAX), ("fc1", 32, I32_MIN, I32_MAX),
                                ("input_native", 128, 0, 127), ("input_serialized", 128, 0, 127),
                                ("output_weights", 128, -128, 127)):
        need(type(row[name]) is list and len(row[name]) == size, "Wrong vector width: " + name)
        for item in row[name]: integer(item, low, high, name)
    slots = [physical_slot(index) for index in range(128)]
    need(type(row["serialized_to_native"]) is list and len(row["serialized_to_native"]) == 128, "Wrong serialized lane map width")
    for index in row["serialized_to_native"]: integer(index, 0, 127, "serialized_to_native")
    need(row["serialized_to_native"] == slots and len(set(slots)) == 128, "AVX2 loader/activation packing differs")
    need(row["input_serialized"] == [row["input_native"][index] for index in slots], "Native and serialized vector mismatch")
    need(row["input_serialized"] == activations(row["fc0"], 7) + activations(row["fc1"], 6), "Captured activation bytes differ from signed quantization")
    bias = integer(row["output_bias"], I32_MIN, I32_MAX, "output_bias")
    affine = bias + sum(weight * value for weight, value in zip(row["output_weights"], row["input_serialized"], strict=True))
    skip = row["fc0"][30] - row["fc0"][31]
    combined = affine + skip
    for value, name in ((affine, "affine"), (skip, "skip"), (combined, "combined")):
        integer(value, I32_MIN, I32_MAX, name)
    scaled = tz(combined * 9600, 16384)
    raw = tz(scaled, 16)
    for name in ("fc2_affine", "skip", "combined", "scaled", "raw_nnue", "ordinary_raw_nnue"):
        integer(row[name], I32_MIN, I32_MAX, name)
    need((affine, skip, combined, scaled, raw, raw) == tuple(row[name] for name in
         ("fc2_affine", "skip", "combined", "scaled", "raw_nnue", "ordinary_raw_nnue")), "FC2 dot, skip, signed scaling or default raw parity differs")
    if readout is not None:
        need((bias, row["output_weights"]) == readout, "Captured output parameters differ from serialized network readout")
    if expected_board is not None:
        import chess
        board = chess.Board(row["supplied_fen"])
        need(board.is_valid() and not board.chess960, "Invalid supplied standard FEN")
        for token in row["moves"]:
            move = board.parse_uci(token)
            need(move in board.legal_moves, "Illegal retained replay prefix")
            board.push(move)
        need(board.fen(en_passant="legal") == row["final_fen"] == expected_board.fen(en_passant="legal"), "Native replay/FEN differs")
        need(any(board.legal_moves), "Unsupported terminal root")
        need(row["in_check"] is board.is_check() and row["ordinary_eval_trace_available"] is (not board.is_check()),
             "In-check ordinary eval-trace availability differs")
        need(row["side_to_move"] == (0 if board.turn == chess.WHITE else 1) and row["rule50"] == board.halfmove_clock, "Native replay context differs")
        need(mapping is not None and len(mapping) == 192 and mapping[raw_route(board)] == head, "Actual stored physical head differs from native fallback route")
        need(row["raw_offset_min"] == -24 and row["raw_offset_max"] == 24 and row["cp_perspective"] == "side_to_move",
             "Unknown raw-offset/CP perspective contract")
        expected_static = [static_zero(raw + offset, board) for offset in range(-24, 25)]
        expected_cp = [to_cp(value, board) for value in expected_static]
        for name in ("raw_offset_static_zero", "raw_offset_cp"):
            need(type(row[name]) is list and len(row[name]) == 49, "Wrong raw offset lookup width")
            for value in row[name]: integer(value, I32_MIN, I32_MAX, name)
        need(row["raw_offset_static_zero"] == expected_static and row["raw_offset_cp"] == expected_cp
             and row["helper_static_zero"] == expected_static[24] and row["helper_static_cp"] == expected_cp[24],
             "Native CP lookup does not match exact source-derived material/rule50 scaling")
        if board.is_check():
            need(row["ordinary_static_zero"] is row["ordinary_static_cp"] is None and row["static_loss_available"] is False
                 and row["static_loss_unavailable_reason"] == "in_check", "In-check helper result was promoted to ordinary static target")
        else:
            need(row["ordinary_static_zero"] == expected_static[24] and row["ordinary_static_cp"] == expected_cp[24]
                 and row["static_loss_available"] is True and row["static_loss_unavailable_reason"] is None,
                 "Ordinary static target availability differs")
    return {"physical_head": head, "raw_nnue": raw, "serialized_lanes": 128, "exact_integer_parity": True}
