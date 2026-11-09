"""Permute paired channels of the original test1757 NNUE, in serialized order.

The paired FT/FC0 symmetry is used by official-stockfish/nnue-pytorch's ftperm.py.
This editor implements the on-disk format read by the Stockfish developers' code
and xu-shawn's test1757 reader (xu-shawn/Stockfish at d5a2bda7f028430f79e1789d47ae3228defb843b).
Neither the engine's load-time SIMD permutations nor a floating-point model
are involved. This module does not search for a permutation or evaluate a net.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import tempfile

import numpy as np


PARENT_SHA256 = "18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf"
PARENT_BYTES = 138_360_528
VERSION, ROUTE_TAG, FT_TAG, STACK_TAG = 0x6A448AFA, 0x5A7AB1E5, 0xA3060B95, 0x63337116
NETWORK_TAG = ROUTE_TAG ^ FT_TAG ^ STACK_TAG


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _padded(width):
    # AffineTransform{,SparseInput}::PaddedInputDimensions; in-file row stride.
    return (width + 31) // 32 * 32


@dataclass(frozen=True)
class Geometry:
    half: int
    feature_rows: tuple  # FullThreats, PP_3Wide, K32H3 (all three material blocks)
    fc0: int
    fc1: int
    raw_routes: int
    stored: int
    description_bytes: int
    fallback_sha256: str

    @property
    def lanes(self):
        return 2 * self.half

    @property
    def stack_bytes(self):
        return (4 + (4 + _padded(self.lanes)) * self.fc0
                + (4 + _padded(2 * self.fc0)) * self.fc1
                + 4 + _padded(2 * self.fc0 + 2 * self.fc1))

    @property
    def bias_offset(self):
        return 12 + self.description_bytes + 12 + 2 * self.raw_routes + 4

    @property
    def stacks_start(self):
        return self.bias_offset + (2 + sum(self.feature_rows)) * self.lanes

    @property
    def size(self):
        return self.stacks_start + self.stored * self.stack_bytes


TEST1757 = Geometry(512, (59_808, 96 * 95 // 2, 3 * 32 * 11 * 64), 32, 32, 192, 92, 84,
                    "166ea64eb3e4da682ba3394b0a7f68de6ca6e01daae472f8c85fbc5ad7e27dd7")


@dataclass(frozen=True)
class Layout:
    spec: Geometry
    bias_offset: int
    plane_offsets: tuple
    stacks_start: int
    fallback: tuple
    raw_to_physical: tuple


def parse_layout(data, spec=TEST1757):
    """Validate the sequential reader, including every stored tag and exact EOF."""
    _require(len(data) == spec.size, "wrong file length for this format")
    version, net_tag, desc_bytes = struct.unpack_from("<3I", data)
    _require((version, net_tag, desc_bytes) == (VERSION, NETWORK_TAG, spec.description_bytes),
             "invalid file version, network tag or description length")
    cursor = 12 + desc_bytes
    route_tag, raw, stored = struct.unpack_from("<3I", data, cursor)
    _require((route_tag, raw, stored) == (ROUTE_TAG, spec.raw_routes, spec.stored),
             "wrong fallback tag or stack counts")
    cursor += 12
    fallback = struct.unpack_from(f"<{raw}H", data, cursor)
    _require(all(f < raw for f in fallback), "fallback route out of range")
    roots = [i for i, f in enumerate(fallback) if i == f]
    _require(len(roots) == stored, "fallback root count differs from stored count")
    # The engine numbers self-roots in ascending raw index and rejects multi-hop aliases.
    index = {root: physical for physical, root in enumerate(roots)}
    _require(all(f in index for f in fallback), "fallback target is not a self-root")
    mapping = tuple(index[f] for f in fallback)
    _require(hashlib.sha256(data[cursor:cursor + 2 * raw]).hexdigest() == spec.fallback_sha256,
             "fallback table SHA-256 mismatch")
    cursor += 2 * raw
    _require(struct.unpack_from("<I", data, cursor)[0] == FT_TAG, "wrong transformer tag")
    bias_offset = cursor + 4
    cursor = bias_offset + 2 * spec.lanes
    plane_offsets = []
    for rows in spec.feature_rows:
        plane_offsets.append(cursor)
        cursor += rows * spec.lanes
    stacks_start = cursor
    for index in range(stored):
        _require(struct.unpack_from("<I", data, cursor)[0] == STACK_TAG, f"wrong stack tag {index}")
        cursor += spec.stack_bytes
    _require((bias_offset, stacks_start, cursor) == (spec.bias_offset, spec.stacks_start, len(data)),
             "FT/dense/EOF boundary mismatch")
    return Layout(spec, bias_offset, tuple(plane_offsets), stacks_start, fallback, mapping)


def verify_parent(data):
    """Authenticate the full original donor before using its views."""
    _require(len(data) == PARENT_BYTES and hashlib.sha256(data).hexdigest() == PARENT_SHA256,
             "input is not the pinned original test1757 NNUE")
    return parse_layout(data)


@dataclass(frozen=True)
class FTViews:
    ft_biases: np.ndarray
    threat_weights: np.ndarray
    pair_weights: np.ndarray
    psq_weights: np.ndarray


def ft_views(data, layout=None):
    """Read-only serialized arrays backed by data; keep its buffer open while in use."""
    layout = parse_layout(data) if layout is None else layout
    spec = layout.spec
    biases = np.frombuffer(data, "<i2", spec.lanes, layout.bias_offset)
    planes = [np.frombuffer(data, "i1", rows * spec.lanes, start).reshape(rows, spec.lanes)
              for rows, start in zip(spec.feature_rows, layout.plane_offsets)]
    for arr in (biases, *planes):
        arr.flags.writeable = False
    return FTViews(biases, *planes)


def validate_permutation(permutation, half=512):
    """Return an immutable new-index-to-old-index permutation without coercing inputs."""
    p = tuple(permutation)
    _require(len(p) == half and all(isinstance(x, (int, np.integer))
                                    and not isinstance(x, (bool, np.bool_)) for x in p),
             f"permutation must contain exactly {half} integer indices")
    p = tuple(map(int, p))
    _require(set(p) == set(range(half)), "permutation is not a bijection")
    return p


def inverse_permutation(permutation, half=512):
    inv = [0] * half
    for new, old in enumerate(validate_permutation(permutation, half)):
        inv[old] = new
    return tuple(inv)


def _chunks(data, layout, p, rows_per_block=512):
    # p[j] is the OLD lane to put at NEW lane j. Both FT factors move together.
    # Both STM/opponent FC0 halves then follow their respective product outputs.
    spec = layout.spec
    order = np.array((*p, *(i + spec.half for i in p)), dtype=np.intp)
    yield data[:layout.bias_offset]  # header, opaque description, routes, FT tag
    ft = ft_views(data, layout)
    yield ft.ft_biases[order].astype("<i2", copy=False).tobytes()
    # K32H3 is an FT input-feature table. This reader has no separate PSQT
    # output table. Neither feature-row identities nor material buckets move.
    for plane in (ft.threat_weights, ft.pair_weights, ft.psq_weights):
        for start in range(0, len(plane), rows_per_block):
            yield plane[start:start + rows_per_block, order].tobytes(order="C")
    for i in range(spec.stored):
        start = layout.stacks_start + i * spec.stack_bytes
        stack = bytearray(data[start:start + spec.stack_bytes])
        # Serialized affine weights are output-major rows. Only their logical
        # FC0 columns change; even a nonzero padding byte is preserved.
        stride = _padded(spec.lanes)
        weights = np.frombuffer(stack, "i1", stride * spec.fc0, 4 + 4 * spec.fc0)
        weights = weights.reshape(spec.fc0, stride)
        weights[:, :spec.lanes] = weights[:, order]
        yield stack


def transform(data, permutation, spec=TEST1757):
    """In-memory transform. This validates the format; the CLI also pins the parent."""
    p = validate_permutation(permutation, spec.half)
    return b"".join(_chunks(data, parse_layout(data, spec), p))


def _publish_new(staged, destination):
    # On Windows rename refuses an existing destination. On POSIX use a hard
    # link for the same atomic no-overwrite guarantee. Stage on the same volume.
    if os.name == "nt":
        os.rename(staged, destination)
    else:
        os.link(staged, destination)


def _write_verified(parent, p, output, receipt, spec, expected_sha):
    """Use the same path for miniature fixtures and the fixed production CLI."""
    p = validate_permutation(p, spec.half)
    paths = [Path(parent), Path(output)]
    if receipt is not None:
        paths.append(Path(receipt))
    _require(len(set(path.resolve() for path in paths)) == len(paths), "paths must be distinct")
    for path in paths[1:]:
        if path.exists():
            raise FileExistsError(f"output already exists: {path.name}")
    parent, output = paths[:2]
    with parent.open("rb") as original, tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        _require(os.fstat(original.fileno()).st_size == spec.size, "wrong input size")
        with mmap.mmap(original.fileno(), 0, access=mmap.ACCESS_READ) as data:
            _require(hashlib.sha256(data).hexdigest() == expected_sha, "wrong parent SHA-256")
            layout = parse_layout(data, spec)
            staged = Path(tmp) / "candidate"
            with staged.open("xb") as writer:
                for chunk in _chunks(data, layout, p):
                    writer.write(chunk)

        with staged.open("rb") as reader, mmap.mmap(reader.fileno(), 0, access=mmap.ACCESS_READ) as data:
            candidate = parse_layout(data, spec)
            _require(candidate == layout, "candidate format or fallback changed")
            candidate_sha = hashlib.sha256(data).hexdigest()
            inverse = hashlib.sha256()
            for part in _chunks(data, candidate, inverse_permutation(p, spec.half)):
                inverse.update(part)
            _require(inverse.hexdigest() == expected_sha, "inverse did not recover original bytes")

        result = {"parent_sha256": expected_sha, "candidate_sha256": candidate_sha,
                  "inverse_sha256": inverse.hexdigest(), "bytes": spec.size,
                  "permutation_u16le_sha256": hashlib.sha256(struct.pack(f"<{spec.half}H", *p)).hexdigest(),
                  "convention": "p[new] = old; P = p concatenated with p + half",
                  "feature_rows": list(spec.feature_rows), "ft_bias_offset": layout.bias_offset,
                  "ft_plane_offsets": list(layout.plane_offsets), "stacks_start": layout.stacks_start,
                  "stack_bytes": spec.stack_bytes, "stored_stacks": spec.stored, "raw_routes": spec.raw_routes,
                  "fallback_u16le_sha256": spec.fallback_sha256, "fallback": list(layout.fallback),
                  "raw_to_physical": list(layout.raw_to_physical)}
        if receipt is None:
            _publish_new(staged, output)
        else:
            receipt = Path(receipt)
            with tempfile.TemporaryDirectory(dir=receipt.parent) as tmp_receipt:
                staged_receipt = Path(tmp_receipt) / "receipt"
                with staged_receipt.open("x", encoding="utf-8", newline="\n") as writer:
                    writer.write(json.dumps(result, indent=2) + "\n")
                _publish_new(staged, output)
                try:
                    _publish_new(staged_receipt, receipt)
                except OSError:
                    output.unlink()
                    raise
        return result


def write_candidate(parent, permutation, output, receipt=None):
    """Write one candidate for this donor; never overwrite an existing output."""
    return _write_verified(parent, permutation, output, receipt, TEST1757, PARENT_SHA256)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parent", required=True, type=Path, help="original nn-18cd32e11a9c.nnue")
    ap.add_argument("--permutation", required=True, type=Path, help="bare JSON list of 512 integer indices")
    ap.add_argument("--output", required=True, type=Path, help="new candidate file")
    ap.add_argument("--receipt", type=Path, help="optional new JSON receipt; also printed on stdout")
    args = ap.parse_args()
    try:
        p = json.loads(args.permutation.read_bytes())
        _require(isinstance(p, list), "permutation file must contain a bare JSON array")
        result = write_candidate(args.parent, p, args.output, args.receipt)
    except (ValueError, OSError) as exc:
        ap.exit(1, f"{ap.prog}: {exc}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
