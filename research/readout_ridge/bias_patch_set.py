"""Write one frozen set of FC2 bias deltas into the original test1757 network.

File geometry and routing come from the pinned layout reader for xu-shawn's
test1757. The signed integer scaling follows Stockfish NetworkArchitecture
and Network::evaluate at d5a2bda7f028430f79e1789d47ae3228defb843b.
"""

from collections.abc import Mapping
from functools import lru_cache
import hashlib
import mmap
from numbers import Integral
import os
from pathlib import Path
import struct
import tempfile

import permute_nnue as nnue


WORD = struct.Struct("<i")


def _integer(value):
    return isinstance(value, Integral) and not isinstance(value, bool)


def _validate(deltas, mode):
    if mode not in ("probe", "candidate"):
        raise ValueError("mode must be probe or candidate")
    if isinstance(deltas, (str, bytes, bytearray, Mapping)):
        raise ValueError("deltas must be an ordered sequence of integers")
    try:
        values = tuple(deltas)
    except TypeError as exc:
        raise ValueError("deltas must be an ordered sequence of integers") from exc
    if len(values) != nnue.TEST1757.stored or any(not _integer(d) for d in values):
        raise ValueError("provide exactly 92 integer deltas in physical stack order")
    values = tuple(map(int, values))
    if mode == "probe" and any(d != 512 for d in values):
        raise ValueError("the sensitivity probe requires +512 in every head")
    if mode == "candidate" and any(abs(d) > 256 or d % 16 for d in values):
        raise ValueError("candidate deltas must be multiples of 16 in [-256, 256]")
    return values


@lru_cache(maxsize=1025)
def _bounds(d):
    magnitude = abs(d)
    # q(F) = trunc0(trunc0(9600*F/16384)/16) = trunc0(75*F/2048).
    # On either side of zero a positive step has size floor(t) or ceil(t),
    # t=75*d/2048. Both extrema are attainable over a residue period because
    # 75 and 2048 are coprime. Crossings F=-r give the sum of two floors.
    lo = min(75 * r // 2048 + 75 * (magnitude - r) // 2048 for r in range(magnitude + 1))
    hi = (75 * magnitude + 2047) // 2048
    return (lo, hi) if d >= 0 else (-hi, -lo)


def raw_delta_bounds(delta):
    """Exact inclusive min/max of q(F+delta)-q(F) for representable i32 sums."""
    if not _integer(delta) or not -512 <= int(delta) <= 512:
        raise ValueError("expected a signed integer bias step with magnitude at most 512")
    return _bounds(int(delta))


def _heads(data, layout, deltas):
    spec = layout.spec
    if spec.stored != len(deltas):
        raise ValueError("delta count differs from geometry")
    routes = [[] for _ in deltas]
    for raw, physical in enumerate(layout.raw_to_physical):
        routes[physical].append(raw)
    # The final affine layer stores its single i32 bias and one padded i8 row.
    fc2_width = 2 * (spec.fc0 + spec.fc1)
    tail_size = WORD.size + ((fc2_width + 31) // 32) * 32
    heads = []
    for head, delta in enumerate(deltas):
        offset = layout.stacks_start + (head + 1) * spec.stack_bytes - tail_size
        before, = WORD.unpack_from(data, offset)
        after = before + delta
        if not -(1 << 31) <= after < (1 << 31):
            raise ValueError(f"head {head} edited bias would overflow int32")
        old, new = WORD.pack(before), WORD.pack(after)
        changed = [{"offset": offset + i, "before": a, "after": b}
                   for i, (a, b) in enumerate(zip(old, new)) if a != b]
        heads.append({"physical_head": head, "delta": delta, "bias_before": before, "bias_after": after,
                      "bias_offset": offset, "raw_routes": routes[head], "raw_delta_bounds": list(raw_delta_bounds(delta)),
                      "changed_bytes": changed, "changed_byte_offsets": [c["offset"] for c in changed]})
    return heads


def _stage(staged, original, heads):
    with staged.open("xb") as out, memoryview(original) as data:
        cursor = 0
        for head in heads:
            if head["delta"]:
                offset = head["bias_offset"]
                out.write(data[cursor:offset])
                out.write(WORD.pack(head["bias_after"]))
                cursor = offset + WORD.size
        out.write(data[cursor:])


def _verify_saved(original, saved, layout, heads, expected_sha):
    if nnue.parse_layout(saved, layout.spec) != layout:
        raise ValueError("candidate routing or geometry differs")
    inverse = hashlib.sha256()
    with memoryview(original) as source, memoryview(saved) as candidate:
        cursor = 0
        for head in heads:
            offset = head["bias_offset"]
            if source[cursor:offset] != candidate[cursor:offset]:
                raise ValueError("bytes outside an FC2 bias changed")
            inverse.update(candidate[cursor:offset])
            value, = WORD.unpack_from(candidate, offset)
            restored = value - head["delta"]
            if value != head["bias_after"] or restored != head["bias_before"]:
                raise ValueError("saved bias or inverse differs")
            inverse.update(WORD.pack(restored))
            cursor = offset + WORD.size
        if source[cursor:] != candidate[cursor:]:
            raise ValueError("bytes after final FC2 bias changed")
        inverse.update(candidate[cursor:])
        if inverse.hexdigest() != expected_sha:
            raise ValueError("inverse did not recover the full parent SHA-256")
        return hashlib.sha256(candidate).hexdigest(), inverse.hexdigest()


def _write(parent_path, deltas, output_path, mode, expected_bytes, expected_sha, authenticate):
    values = _validate(deltas, mode)
    parent, output = Path(parent_path), Path(output_path)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output.name}")
    with parent.open("rb") as src:
        if os.fstat(src.fileno()).st_size != expected_bytes:
            raise ValueError("wrong parent size")
        with mmap.mmap(src.fileno(), 0, access=mmap.ACCESS_READ) as original:
            layout = authenticate(original)
            heads = _heads(original, layout, values)
            changed = [h["physical_head"] for h in heads if h["delta"]]
            if changed:
                with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
                    staged = Path(tmp) / "network"
                    _stage(staged, original, heads)
                    with staged.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as saved:
                        candidate_sha, inverse_sha = _verify_saved(original, saved, layout, heads, expected_sha)
                    nnue._publish_new(staged, output)
            else:
                candidate_sha, inverse_sha = _verify_saved(original, original, layout, heads, expected_sha)
    return {"mode": mode, "identity": not changed, "output_written": bool(changed),
            "parent_sha256": expected_sha, "candidate_sha256": candidate_sha, "inverse_sha256": inverse_sha,
            "bytes": expected_bytes, "deltas": list(values), "heads": heads, "changed_heads": changed,
            "changed_byte_count": sum(len(h["changed_bytes"]) for h in heads),
            "affected_raw_routes": [raw for raw, physical in enumerate(layout.raw_to_physical) if values[physical]],
            "raw_to_physical": list(layout.raw_to_physical), "fallback_u16le_sha256": layout.spec.fallback_sha256}


def write_bias_patch_set(parent_path, deltas, output_path, mode):
    """Write an exclusively named verified probe/candidate, or return an identity receipt."""
    return _write(parent_path, deltas, output_path, mode,
                  nnue.PARENT_BYTES, nnue.PARENT_SHA256, nnue.verify_parent)
