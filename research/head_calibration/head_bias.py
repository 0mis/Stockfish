"""Route a standard position and edit one physical FC2 bias in the original test1757.

The on-disk format and layer_stack calculation follow the Stockfish developers
and xu-shawn/Stockfish at d5a2bda7f028430f79e1789d47ae3228defb843b.
"""

import hashlib
import mmap
from numbers import Integral
import os
from pathlib import Path
import struct
import tempfile

import chess

import features_v19
import permute_nnue as nnue


QUEENSIDE = chess.BB_FILE_A | chess.BB_FILE_B | chess.BB_FILE_C | chess.BB_FILE_D
INT32 = struct.Struct("<i")


def head_for_fen(fen, layout):
    """Return (raw route, physical stack) using a layout from the verified parent."""
    board = chess.Board(fen)
    # stack_bucket validates standard chess and counts queens relative to the mover.
    coarse = features_v19.stack_bucket(board)
    pawns = board.pawns
    if not pawns:
        wing = 2
    elif pawns & QUEENSIDE and pawns & ~QUEENSIDE:
        wing = 0
    else:
        wing = 1
    officers = board.occupied & ~(pawns | board.kings)
    many = any((officers & board.occupied_co[color]).bit_count() >= 2 for color in chess.COLORS)
    raw = (coarse * 3 + wing) * 2 + int(many)
    return raw, layout.raw_to_physical[raw]


def raw_from_forwarded(forwarded):
    """Raw STM NNUE after the FC2 affine sum and the signed FC0[30]-FC0[31] skip.

    The donor computes (F*9600/16384)/16 with both divisions toward zero.
    This equals trunc0(75F/2048). For representable F and F+512 the unrounded change is
    18.75. The integer increase is 18 or 19 on one side of zero, and can be
    17 or 18 for a step across zero. Negative steps give the mirrored changes.
    """
    if not isinstance(forwarded, Integral) or isinstance(forwarded, bool):
        raise ValueError("forwarded sum must be an integer")
    forwarded = int(forwarded)
    if not -(1 << 31) <= forwarded < (1 << 31):
        raise ValueError("forwarded sum is outside int32")
    scaled = features_v19._trunc_div(forwarded * 9600, 16384)
    return features_v19._trunc_div(scaled, 16)


def _bias_offset(layout, head):
    # AffineTransform<2*L2+2*L3,1> serializes the i32 bias, then exactly one
    # signed-byte row padded to MaxSimdWidth=32. For test1757 the tail is 132 bytes.
    spec = layout.spec
    fc2_stride = ((2 * (spec.fc0 + spec.fc1) + 31) // 32) * 32
    stack_end = layout.stacks_start + (head + 1) * spec.stack_bytes
    return stack_end - fc2_stride - INT32.size


def _verify_saved(original, saved, layout, offset, before, after, delta, parent_sha):
    if nnue.parse_layout(saved, layout.spec) != layout:
        raise ValueError("saved geometry or routing differs")
    (on_disk,) = INT32.unpack_from(saved, offset)
    if on_disk != after or on_disk - delta != before:
        raise ValueError("saved forward or inverse bias mismatch")
    end = offset + INT32.size
    with memoryview(original) as source, memoryview(saved) as candidate:
        if candidate[:offset] != source[:offset] or candidate[end:] != source[end:]:
            raise ValueError("bytes outside the selected bias changed")
        restored = hashlib.sha256()
        restored.update(candidate[:offset])
        restored.update(INT32.pack(on_disk - delta))
        restored.update(candidate[end:])
        inverse_sha = restored.hexdigest()
        if inverse_sha != parent_sha:
            raise ValueError("inverse did not restore the complete parent SHA-256")
        return hashlib.sha256(candidate).hexdigest(), inverse_sha


def _write_bias(parent_path, target_head, delta, output_path, spec, expected_sha):
    """Shared implementation for the pinned public API and miniature fixtures."""
    if (isinstance(target_head, bool) or not isinstance(target_head, Integral)
            or not 0 <= target_head < spec.stored):
        raise ValueError("physical head outside the stored stack range")
    if isinstance(delta, bool) or not isinstance(delta, Integral) or delta not in (-512, 512):
        raise ValueError("FC2 bias delta must be +512 or -512")
    target_head, delta = int(target_head), int(delta)
    parent, output = Path(parent_path), Path(output_path)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output.name}")

    with parent.open("rb") as source, tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        if os.fstat(source.fileno()).st_size != spec.size:
            raise ValueError("wrong input length")
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as original:
            parent_sha = hashlib.sha256(original).hexdigest()
            if parent_sha != expected_sha:
                raise ValueError("parent SHA-256 mismatch")
            layout = nnue.parse_layout(original, spec)
            offset = _bias_offset(layout, target_head)
            (before,) = INT32.unpack_from(original, offset)
            after = before + delta
            if not -(1 << 31) <= after < (1 << 31):
                raise ValueError("edited bias would overflow int32")
            old_word, new_word = INT32.pack(before), INT32.pack(after)
            staged = Path(tmp) / "candidate"
            with staged.open("xb") as writer, memoryview(original) as data:
                writer.write(data[:offset])
                writer.write(new_word)
                writer.write(data[offset + INT32.size:])
            with staged.open("rb") as reader, mmap.mmap(reader.fileno(), 0, access=mmap.ACCESS_READ) as saved:
                candidate_sha, inverse_sha = _verify_saved(
                    original, saved, layout, offset, before, after, delta, parent_sha)

        affected_routes = [raw for raw, physical in enumerate(layout.raw_to_physical) if physical == target_head]
        changed_offsets = [offset + i for i in range(INT32.size) if old_word[i] != new_word[i]]
        report = {"parent_sha256": parent_sha, "candidate_sha256": candidate_sha, "inverse_sha256": inverse_sha,
                  "bytes": spec.size, "target_head": target_head, "delta": delta,
                  "bias_before": before, "bias_after": after, "bias_offset": offset, "bias_bytes": INT32.size,
                  "before_i32le_hex": old_word.hex(), "after_i32le_hex": new_word.hex(),
                  "changed_byte_count": len(changed_offsets), "changed_byte_offsets": changed_offsets,
                  "affected_raw_routes": affected_routes, "fallback_u16le_sha256": spec.fallback_sha256}
        nnue._publish_new(staged, output)
        return report


def write_bias_candidate(parent_path, target_head, delta, output_path):
    """Exclusively publish a verified one-word edit of the original 18cd network."""
    return _write_bias(parent_path, target_head, delta, output_path, nnue.TEST1757, nnue.PARENT_SHA256)
